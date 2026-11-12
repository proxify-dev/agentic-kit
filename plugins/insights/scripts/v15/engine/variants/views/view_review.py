"""The page's first tab: the person's setup at a glance, with the depth one click away.

Why it looks like this (his 11-11, on the written review this tab used to be): "Users get lost when they see it… too
much to read… be more creative." It was a long lede, then one card per project with two long paragraphs. Now the first
screen answers four questions a non-engineer can take in within seconds, each as a picture before any sentence:
  1. Where am I?            the level mark and a four-step ladder, each step with how many of its things are in place
  2. What does my agent know? one row per project, three cells (rules, corrections, memory), each a state as icon + word
  3. What do I change first?  ONE card: the consequence in plain words, a picture of it, "Do this", "Why it pays"
  4. What already works?      up to three receipts: what, and the line from their file that shows it
Then each project, collapsed, and "About this report". The readers' prose (RUN_DIR/setup-read.json, setup-snapshot
experiments/2026-11-11-setup-read) stays as data and opens on a click: a receipt under a cell, a card's details, a
project's row. Rules for the words on the first screen: at most two lines each, every number next to what it means,
no jargon ("tokens", "scoped", "compaction", "MCP" never lead), each state an icon and a word, colour only adds.

The files are other steps' output and may be missing or half-filled (a reader that failed is not a stop): every key is
optional, and with no read at all the tab says so and still shows the outline the code mapped."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..")))
import things as level_things  # noqa: E402  which level read is in use, its things, and the one count of each number
import wording  # noqa: E402  the one plural and noun
from wording import plural  # noqa: E402
sys.path.insert(0, HERE)
import view_mylevel  # noqa: E402  the same level names as the Your level tab: the two tabs never disagree

ORDER = 0
TITLE = "A review of your setup"
QUESTION = "Your level, what your agent knows, and the one thing to change first."
PROXIFY_ONLY = False
HIDE_HEADER = True  # frame: the title stays for screen readers and the window title; the level opens the tab instead

SHOW = 3        # receipts and extra changes on the first screen; the rest opens on click
MORE_SHOWN = 3  # lines under the first change; a project's other changes stay in its row under "Each project"
RECORDS_TAG = "From Claude Code's records"   # a change the code counted, not one the reader judged
HABIT_TAG = "A habit seen in Claude Code's records"
TREE_FILES = 40  # files drawn in one project's tree; the rest is a count
WORDS_PER_PAGE, WORDS_PER_TOKEN = 500, 0.75  # a printed page; English words per token
WHY_WORDS = 20   # the hero's one sentence

PARTS = (("rules", "Rules"), ("corrections", "Corrections"), ("memory", "Memory"))
PART_NAME = {"rules": "Rules that reach the agent where they apply", "corrections": "Corrections that stick",
             "memory": "Memory the next session reads"}
PART_WORD = {"rules": "rules", "corrections": "corrections", "memory": "memory"}
# a state as the page says it: an icon and a word; colour (the class) only adds
STATE = {"shown": ("shown", "Shown"), "partly": ("partly", "Partly"), "notshown": ("none", "Not shown"),
         "unread": ("unread", "Can't tell")}
# a habit Claude Code's records show, when the step that found it gave no cost or step of its own
HOLD_COST = ("A session that runs until it compacts on its own loses its early decisions each time: the agent keeps "
             "working from a summary of a summary, and the rules you wrote get followed less.")
HOLD_DO = ("Give each task its own session. When one runs long, type `/compact` with what to keep, or `/clear` and "
           "start again from a short handoff note.")

_SVG = ('<svg class="{c}" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{d}</svg>')
ICON = {
    "shown": '<circle cx="8" cy="8" r="6.25"/><path d="M5.4 8.3l1.8 1.8 3.4-3.8"/>',
    "partly": '<circle cx="8" cy="8" r="6.25"/><path d="M8 1.75a6.25 6.25 0 0 0 0 12.5z" fill="currentColor"/>',
    "none": '<circle cx="8" cy="8" r="6.25"/><path d="M5.5 8h5"/>',
    "unread": '<circle cx="8" cy="8" r="6.25" stroke-dasharray="2.2 2.2"/><path d="M6.5 6.4a1.6 1.6 0 1 1 2.2 1.5c-.4.2-.7.5-.7 1v.2M8 11.1v.1"/>',
    "file": '<path d="M4 1.75h5.25L12 4.5v9.75H4z"/><path d="M9.25 1.75V4.5H12"/>',
    "folder": '<path d="M1.75 4.25a1 1 0 0 1 1-1h3.5l1.5 1.5h5.5a1 1 0 0 1 1 1v6.5a1 1 0 0 1-1 1h-10.5a1 1 0 0 1-1-1z"/>',
    "chev": '<path d="M6 4l4 4-4 4"/>',
    "arrow": '<path d="M3 8h10M9 4l4 4-4 4"/>',
}
# a memory store's shape (setup_map.py names it): a small drawing, and the words for it
SHAPE = {
    "flat-log": ("A running log", '<path d="M3 3.5h10M3 6.5h10M3 9.5h10M3 12.5h6"/>'),
    "single-file": ("One file", ICON["file"]),
    "indexed": ("An index, then notes", '<circle cx="8" cy="3.5" r="1.75"/><path d="M8 5.25v2.5M3.5 10V7.75h9V10M8 7.75V10"/>'
                '<circle cx="3.5" cy="12" r="1.5"/><circle cx="8" cy="12" r="1.5"/><circle cx="12.5" cy="12" r="1.5"/>'),
    "layered": ("An index, folders, then notes", '<circle cx="8" cy="2.75" r="1.5"/><path d="M8 4.25v1.5M4.5 7.25v-1.5h7v1.5"/>'
                '<circle cx="4.5" cy="8.5" r="1.25"/><circle cx="11.5" cy="8.5" r="1.25"/>'
                '<path d="M4.5 9.75v1.25M2.75 12.25V11h3.5v1.25M11.5 9.75v1.25M9.75 12.25V11h3.5v1.25"/>'
                '<circle cx="2.75" cy="13.5" r=".9"/><circle cx="6.25" cy="13.5" r=".9"/><circle cx="9.75" cy="13.5" r=".9"/>'
                '<circle cx="13.25" cy="13.5" r=".9"/>'),
    "linked": ("Notes that link to each other", '<path d="M4 4.5l7.5-1M4 4.5l4 4.5M11.5 3.5L8 9M8 9l-4.5 3.5M8 9l4.5 3M3.5 12.5l9 -.5"/>'
               '<circle cx="4" cy="4.5" r="1.5"/><circle cx="11.5" cy="3.5" r="1.5"/><circle cx="8" cy="9" r="1.5"/>'
               '<circle cx="3.5" cy="12.5" r="1.5"/><circle cx="12.5" cy="12" r="1.5"/>'),
    "empty": ("Empty", '<rect x="2.5" y="2.5" width="11" height="11" rx="2" stroke-dasharray="2 2"/>'),
}
SHAPED = ("indexed", "layered", "linked")
# where a file of theirs reaches the agent, as the outline's sections name it (setup_map.py's setup-map.md)
LOADS = {"always": ("Always loaded", "always"), "scoped": ("Loads in its folder", "scoped"), "demand": ("On demand", "demand")}

# The first change, by card id (setup_read.py `cards`): the consequence as a headline, the step and the pay-off in one
# line each. The card's own sentences (what was counted, what it costs, the full step) open under "What we saw".
CARD_SAYS = {
    "starting-context": {"do": "Switch off the tools and plugins this project doesn't use.",
                         "do_rules": "Cut the rule file every session loads down to what every task needs.",
                         "pays": "All of it is read again on every turn: a lighter start costs less and keeps your rules in focus."},
    "unused-setup": {"do": "Move out the skills you don't use, or sharpen their one-line description.",
                     "do_others": "Uninstall what you didn't use this month; it comes back the day you need it.",
                     "pays": "Fewer, clearer choices, so the agent picks the skill you meant."},
    "rules-only-grow": {"head": "A rule file every session loads only ever grows, so each rule weighs less",
                        "do": "Cut what the agent gets right unasked; move one area's rules into that folder.",
                        "pays": "A short file is one the agent actually follows."},
    "session-scope": {"head": "Long sessions make your agent forget what you decided early on",
                      "do": "Give each task its own session, and hand over with a short note when one runs long.",
                      "pays": "The agent works from your words, not from a summary of a summary."},
}
CARD_SAYS["endless-sessions"] = CARD_SAYS["session-scope"]
UNITS = ("words", "tokens", "sessions", "compactions", "skills", "files", "notes", "links", "times", "commits",
         "changes", "servers", "plugins", "hours", "days", "lines")

CSS = """
.v-review{--rv-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-variant-numeric:normal}
.v-review .lad,.v-review .key,.v-review .big,.v-review .cm .n,.v-review .ft-r .w{font-variant-numeric:tabular-nums}
.v-review>header.rv-sr,.v-review .rv-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.v-review p{margin:0}
.v-review code{font:12.5px/1.5 var(--rv-mono);background:#F4F5F7;border-radius:4px;padding:1px 4px}
.v-review svg{flex:none}
.v-review .rv-sec{margin-top:48px}
.v-review .rv-sec>h3{font-size:18px;font-weight:600;line-height:1.35;margin:0;color:#15172F;letter-spacing:-.005em}
.v-review .rv-sec>.rv-sub{color:#667085;margin:4px 0 16px;max-width:72ch}
.v-review .rv-link{display:inline-flex;align-items:center;gap:4px;color:#3C3ED4;font-weight:500;font-size:13px;text-decoration:none}
.v-review .rv-link:hover{text-decoration:underline}
.v-review .rv-link svg{width:14px;height:14px}
.v-review summary{list-style:none}
.v-review summary::-webkit-details-marker{display:none}
.v-review .rv-chev{width:16px;height:16px;color:#667085;transition:transform .15s ease}
.v-review details[open]>summary .rv-chev{transform:rotate(90deg)}
.v-review .rv-toggle{display:inline-flex;align-items:center;gap:6px;margin-top:12px;color:#3C3ED4;font-size:13px;font-weight:500}
.v-review .rv-toggle .rv-chev{color:#3C3ED4;width:14px;height:14px}

/* level */
.v-review .lv{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:32px;align-items:center;background:#FFFFFF;
  border:1px solid #EAECF0;border-radius:20px;padding:28px 32px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.v-review .lv-id{display:flex;gap:24px;align-items:center;min-width:0}
.v-review .lv-mark{flex:none;display:grid;place-items:center;width:96px;height:96px;border-radius:24px;background:#5258FB;
  color:#FFFFFF;font-size:40px;font-weight:600;letter-spacing:-.02em;line-height:1}
.v-review .lv-over{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px;font-weight:600;letter-spacing:.06em;
  text-transform:uppercase;color:#667085}
.v-review .lv-could{font-size:12px;font-weight:600;letter-spacing:0;text-transform:none;color:#3C3ED4;border:1px dashed #999AFD;
  border-radius:999px;padding:1px 8px}
.v-review .lv-name{font-size:24px;font-weight:600;line-height:1.25;letter-spacing:-.01em;color:#15172F;margin:4px 0 6px}
.v-review .lv-why{color:#475467;font-size:15px;line-height:1.5;max-width:60ch}
.v-review .lad{display:grid;gap:10px;text-decoration:none;color:inherit;border-radius:12px;padding:4px}
.v-review .lad ol{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(4,68px);gap:12px;align-items:end}
.v-review .lad li{display:grid;gap:6px;justify-items:center}
.v-review .lad .bar{width:100%;height:var(--h);border-radius:10px 10px 6px 6px;background:#F4F5F7;border:1px dashed #D0D5DD;
  display:flex;flex-wrap:wrap;align-content:flex-start;justify-content:center;gap:4px;padding:8px 6px}
.v-review .lad .dot{width:6px;height:6px;border-radius:50%;border:1.5px solid #D0D5DD}
.v-review .lad .bar{gap:3px}
.v-review .lad li.below .bar{background:#EEEFFE;border:1px solid #D5D5FE}
.v-review .lad li.below .dot{border-color:#999AFD}.v-review .lad li.below .dot.met{background:#5258FB;border-color:#5258FB}
.v-review .lad li.you .bar{background:#5258FB;border:1px solid #5258FB;box-shadow:0 6px 16px -6px rgba(82,88,251,.55)}
.v-review .lad li.you .dot{border-color:#B1B8FF}.v-review .lad li.you .dot.met{background:#FFFFFF;border-color:#FFFFFF}
.v-review .lad li.could .bar{border-color:#999AFD}
.v-review .lad b{font-size:13px;font-weight:600;color:#667085;line-height:1}
.v-review .lad li.below b{color:#272594}.v-review .lad li.you b{color:#3C3ED4}
.v-review .lad small{font-size:12px;color:#667085;line-height:1;white-space:nowrap}
.v-review .lad .lad-foot{display:flex;justify-content:space-between;gap:12px;align-items:center;font-size:12px;color:#667085}
.v-review .lad:hover .rv-link{text-decoration:underline}

/* what your agent knows */
.v-review .kn{background:#FFFFFF;border:1px solid #EAECF0;border-radius:16px;overflow:hidden}
.v-review .kn-head,.v-review .kn-main{display:grid;grid-template-columns:minmax(0,1fr) repeat(3,minmax(0,172px));gap:8px;align-items:center;padding:0 20px}
.v-review .kn-head{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#667085;background:#F9FAFB;
  border-bottom:1px solid #EAECF0;padding-top:10px;padding-bottom:10px}
.v-review .kn-head span:not(:first-child){padding-left:12px}
.v-review .kn-row+.kn-row{border-top:1px solid #EAECF0}
.v-review .kn-main{padding-top:12px;padding-bottom:12px}
.v-review .kn-name{min-width:0}
.v-review .kn-name b{display:block;font-size:15px;font-weight:600;color:#15172F;overflow-wrap:anywhere}
.v-review .kn-name span{display:block;font-size:13px;color:#667085;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v-review .kn-c{all:unset;box-sizing:border-box;display:flex;align-items:center;gap:8px;min-height:40px;padding:6px 10px 6px 12px;
  border-radius:10px;border:1px solid transparent;cursor:pointer;font-size:14px;font-weight:500;color:#15172F}
.v-review .kn-c:hover{background:#F9FAFB;border-color:#EAECF0}
.v-review .kn-c:focus-visible{outline:2px solid #5258FB;outline-offset:1px}
.v-review .kn-c[aria-expanded=true]{background:#F5F5FF;border-color:#D5D5FE}
.v-review .kn-c .lab{display:none}
.v-review .kn-c .shape{width:18px;height:18px;color:#667085;margin-left:auto}
.v-review .kn-c .rv-chev{width:14px;height:14px;margin-left:auto;color:#949BAD}
.v-review .kn-c .shape+.rv-chev{margin-left:0}
.v-review .kn-c[aria-expanded=true] .rv-chev{transform:rotate(90deg);color:#3C3ED4}
.v-review .st{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.v-review .st svg{width:16px;height:16px}
.v-review .st.shown svg{color:#039855}.v-review .st.partly svg{color:#B54708}
.v-review .st.none svg,.v-review .st.unread svg{color:#667085}
.v-review .st.none,.v-review .st.unread{color:#475467}
.v-review .kn-rc{margin:0 20px 16px;padding:16px 20px;background:#F9FAFB;border:1px solid #EAECF0;border-radius:12px;display:grid;gap:10px}
.v-review .kn-rc[hidden]{display:none}
.v-review .rc-top{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;font-size:14px}
.v-review .rc-top b{font-weight:600;color:#15172F}
.v-review .rc-top .in{color:#667085}
.v-review .rc-why{color:#344054;max-width:90ch}
.v-review .rc-shape{display:flex;gap:8px;align-items:center;color:#475467;font-size:13px}
.v-review .rc-shape svg{width:18px;height:18px;color:#3C3ED4}
.v-review .rc-next{color:#344054;max-width:90ch}
.v-review .rc-next b,.v-review .lbl{display:block;font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#3C3ED4;margin-bottom:2px}
.v-review .rc-none{color:#667085;font-size:13px}

/* a quoted line from their file: the receipt */
.v-review .q{margin:0;background:#FFFFFF;border:1px solid #EAECF0;border-left:3px solid #5258FB;border-radius:8px;padding:10px 14px;min-width:0}
.v-review .q blockquote{margin:0;font:12.5px/1.6 var(--rv-mono);color:#15172F;overflow-wrap:anywhere;white-space:pre-wrap}
.v-review .q figcaption{display:flex;gap:6px;align-items:center;margin-top:6px;font:12px/1.4 var(--rv-mono);color:#667085;overflow-wrap:anywhere}
.v-review .q figcaption svg{width:12px;height:12px}

/* change this first */
.v-review .cf{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);background:#F5F5FF;border:1px solid #D5D5FE;
  border-radius:20px;overflow:hidden;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.v-review .cf-main{padding:28px 32px;display:grid;gap:16px;align-content:start;min-width:0}
.v-review .cf-tag{display:inline-flex;align-items:center;gap:6px;width:max-content;max-width:100%;font-size:12px;font-weight:600;
  color:#3C3ED4;background:#FFFFFF;border:1px solid #D5D5FE;border-radius:999px;padding:2px 10px}
.v-review .cf-h{font-size:22px;font-weight:600;line-height:1.3;letter-spacing:-.01em;color:#15172F;margin:0;max-width:30ch}
.v-review .cf-side{background:#FFFFFF;border-left:1px solid #D5D5FE;padding:28px 32px;display:grid;gap:20px;align-content:center}
.v-review .cf-side p{color:#15172F;font-size:15px;line-height:1.5}
.v-review .cf-side .lbl{margin-bottom:4px}
.v-review .cf-side .pays .lbl{color:#039855}
.v-review .cf-more>summary{display:inline-flex;align-items:center;gap:6px;color:#3C3ED4;font-size:13px;font-weight:500}
.v-review .cf-more>summary .rv-chev{color:#3C3ED4;width:14px;height:14px}
.v-review .cf-more .cf-full{display:grid;gap:10px;margin-top:12px;font-size:13px;color:#344054}
.v-review .cf-full .lbl{color:#667085}
.v-review .pages{display:flex;flex-wrap:wrap;gap:5px;max-width:540px}
.v-review .pages i{width:14px;height:18px;border-radius:2px 5px 2px 2px;background:#FFFFFF;border:1px solid #C7C9FD}
.v-review .pages i.own{background:#5258FB;border-color:#5258FB}
.v-review .key{display:flex;flex-wrap:wrap;gap:6px 20px;font-size:13px;color:#475467}
.v-review .key>span{display:inline-flex;align-items:center;gap:8px}
.v-review .key i{width:10px;height:12px;border-radius:2px;background:#FFFFFF;border:1px solid #C7C9FD}
.v-review .key i.own{background:#5258FB;border-color:#5258FB}
.v-review .key i.dot{width:9px;height:9px;border-radius:50%}
.v-review .key b{font-weight:600;color:#15172F}
.v-review .fine{font-size:12px;color:#667085}
.v-review .dots{display:flex;flex-wrap:wrap;gap:5px;max-width:520px}
.v-review .dots i{width:9px;height:9px;border-radius:50%;border:1.5px solid #C7C9FD;background:#FFFFFF}
.v-review .dots i.on,.v-review .key i.dot.on{background:#5258FB;border-color:#5258FB}
.v-review .big{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.v-review .big b{font-size:48px;font-weight:600;line-height:1;letter-spacing:-.02em;color:#3C3ED4}
.v-review .big span{font-size:15px;color:#475467}
.v-review .cm{margin-top:16px}
.v-review .cm>h4{font-size:14px;font-weight:600;color:#15172F;margin:0 0 8px}
.v-review .cm ul{list-style:none;margin:0;padding:0;background:#FFFFFF;border:1px solid #EAECF0;border-radius:14px;overflow:hidden}
.v-review .cm li+li{border-top:1px solid #EAECF0}
.v-review .cm summary{display:grid;grid-template-columns:auto minmax(0,1fr) auto auto;gap:12px;align-items:center;padding:12px 16px}
.v-review .cm summary:hover{background:#F9FAFB}
.v-review .cm .n{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;background:#F4F5F7;color:#475467;font-size:12px;font-weight:600}
.v-review .cm .t{color:#15172F;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v-review .cm .src{font-size:12px;color:#667085;white-space:nowrap}
.v-review .cm .body{display:grid;gap:10px;padding:4px 16px 16px 50px;color:#344054;font-size:14px;max-width:96ch}
.v-review .cm .body .lbl{color:#667085}
.v-review .cm .body .do .lbl{color:#3C3ED4}
.v-review .cm-rest>summary{display:inline-flex;align-items:center;gap:6px;margin-top:10px;color:#3C3ED4;font-size:13px;font-weight:500}
.v-review .cm-rest>summary .rv-chev{color:#3C3ED4;width:14px;height:14px}
.v-review .cm-rest ul{margin-top:8px}
.v-review .cm-left{font-size:13px;color:#667085;margin-top:10px !important}

/* what works */
.v-review .wk-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.v-review .wk{display:flex;flex-direction:column;gap:12px;background:#FFFFFF;border:1px solid #EAECF0;border-radius:16px;padding:20px;min-width:0}
.v-review .wk-top{display:flex;justify-content:space-between;gap:8px;align-items:center;font-size:12px}
.v-review .wk-ok{display:inline-flex;align-items:center;gap:6px;font-weight:600;color:#027A48}
.v-review .wk-ok svg{width:16px;height:16px;color:#039855}
.v-review .wk-in{color:#667085;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.v-review .wk h4{margin:0;font-size:15px;font-weight:600;line-height:1.45;color:#15172F}
.v-review .wk details{margin-top:auto}
.v-review .wk details>summary{display:inline-flex;align-items:center;gap:6px;color:#3C3ED4;font-size:13px;font-weight:500}
.v-review .wk details>summary .rv-chev{color:#3C3ED4;width:14px;height:14px}
.v-review .wk details p{margin-top:8px;color:#344054;font-size:14px}
.v-review .wk-more>summary{display:inline-flex;align-items:center;gap:6px;margin-top:12px;color:#3C3ED4;font-size:13px;font-weight:500}
.v-review .wk-more>summary .rv-chev{color:#3C3ED4;width:14px;height:14px}
.v-review .wk-more .wk-grid{margin-top:12px}

/* each project */
.v-review .pj-list{display:grid;gap:8px}
.v-review .pj{background:#FFFFFF;border:1px solid #EAECF0;border-radius:14px}
.v-review .pj>summary{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:16px;align-items:center;padding:14px 20px}
.v-review .pj>summary:hover{background:#F9FAFB;border-radius:14px}
.v-review .pj-sts{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.v-review .pj-sts .st{font-size:12px;font-weight:500;padding:3px 8px 3px 6px;border:1px solid #EAECF0;border-radius:999px;color:#344054}
.v-review .pj-sts .st svg{width:14px;height:14px}
.v-review .pj-body{padding:4px 20px 20px;display:grid;grid-template-columns:minmax(0,1fr);gap:20px;border-top:1px solid #EAECF0}
.v-review .pj-hl{font-weight:600;color:#15172F;margin-top:16px !important;max-width:80ch}
.v-review .pj-sum{color:#344054;max-width:80ch;margin-top:6px !important}
.v-review .pj-cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.v-review .pj-cols h5,.v-review .pj-body>h5{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#667085;margin:0 0 8px}
.v-review .pj-cols ul{margin:0;padding:0;list-style:none;display:grid;gap:8px}
.v-review .pj-cols li{display:flex;gap:8px;color:#344054;font-size:14px}
.v-review .pj-cols li svg{width:16px;height:16px;margin-top:3px}
.v-review .pj-cols .ok svg{color:#039855}.v-review .pj-cols .to svg{color:#3C3ED4}

/* the outline as a file tree */
.v-review .ft{background:#F9FAFB;border:1px solid #EAECF0;border-radius:12px;padding:12px 16px;overflow-x:auto}
.v-review .ft-title{font-size:13px;font-weight:600;color:#15172F;margin:12px 0 6px}
.v-review .ft-title:first-child{margin-top:0}
.v-review .ft-note{font-size:12px;color:#667085;margin:0 0 8px}
.v-review .ft ul{list-style:none;margin:0;padding:0}
.v-review .ft ul ul{margin-left:7px;padding-left:14px;border-left:1px solid #D0D5DD}
.v-review .ft li{min-width:max-content}
.v-review .ft-r{display:flex;align-items:center;gap:8px;min-height:28px;font:12.5px/1.4 var(--rv-mono);color:#15172F}
.v-review .ft-r svg{width:14px;height:14px;color:#667085}
.v-review .ft-r.dir{color:#475467}
.v-review .ft-r .w{font:12px/1 Inter,system-ui,sans-serif;color:#667085}
.v-review .ld{font:500 11px/1 Inter,system-ui,sans-serif;border-radius:999px;padding:3px 8px;white-space:nowrap}
.v-review .ld.always{background:#EEEFFE;color:#3C3ED4}
.v-review .ld.scoped{background:#F6FEF9;color:#027A48;box-shadow:inset 0 0 0 1px #C4F3D8}
.v-review .ld.demand{background:#FFFFFF;color:#475467;box-shadow:inset 0 0 0 1px #EAECF0}
.v-review .ft-more{font:12px/1.4 Inter,system-ui,sans-serif;color:#667085;padding:4px 0 0 22px}
.v-review .ft-mem{display:grid;gap:6px;margin-top:12px}
.v-review .ft-mem div{display:flex;align-items:center;gap:8px;font-size:13px;color:#344054;flex-wrap:wrap}
.v-review .ft-mem svg{width:18px;height:18px;color:#3C3ED4}
.v-review .ft-mem code{background:#FFFFFF}
.v-review .ft-chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:10px;font-size:13px;color:#475467}
.v-review .ft-chips b{font-weight:600;color:#344054;margin-right:2px}
.v-review .ft-chips span{font:12px/1.4 var(--rv-mono);background:#FFFFFF;border:1px solid #EAECF0;border-radius:6px;padding:1px 6px;color:#344054}

/* no read, about */
.v-review .rv-empty{background:#FFFFFF;border:1px dashed #D0D5DD;border-radius:16px;padding:24px 28px;display:grid;gap:6px}
.v-review .rv-empty b{font-size:16px;font-weight:600;color:#15172F}
.v-review .rv-empty p{color:#475467}
.v-review .none-yet{color:#667085;background:#FFFFFF;border:1px dashed #D0D5DD;border-radius:14px;padding:16px 20px}
.v-review .about{margin-top:56px;border-top:1px solid #EAECF0;padding-top:16px}
.v-review .about>summary{display:inline-flex;align-items:center;gap:6px;color:#475467;font-size:13px;font-weight:500}
.v-review .about dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:8px 24px;margin:16px 0 0;font-size:13px}
.v-review .about dt{color:#667085}
.v-review .about dd{margin:0;color:#344054;overflow-wrap:anywhere}

@media (max-width:900px){
  .v-review .lv{grid-template-columns:1fr;gap:24px}
  .v-review .cf{grid-template-columns:1fr}
  .v-review .cf-side{border-left:0;border-top:1px solid #D5D5FE}
}
@media (max-width:720px){
  .v-review .rv-sec{margin-top:40px}
  .v-review .lv{padding:20px;border-radius:16px}
  .v-review .lv-id{gap:16px;align-items:flex-start}
  .v-review .lv-mark{width:64px;height:64px;border-radius:16px;font-size:28px}
  .v-review .lv-name{font-size:20px}
  .v-review .lv-why{font-size:14px}
  .v-review .lad ol{grid-template-columns:repeat(4,minmax(0,1fr))}
  .v-review .kn-head{display:none}
  .v-review .kn-main{grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;padding:14px 12px}
  .v-review .kn-name{grid-column:1/-1;padding:0 4px 4px}
  .v-review .kn-c{flex-wrap:wrap;gap:2px 6px;padding:8px;align-content:flex-start;border-color:#EAECF0;font-size:13px}
  .v-review .kn-c .lab{display:block;width:100%;font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:#667085}
  .v-review .kn-c .rv-chev{display:none}
  .v-review .kn-c .shape{width:16px;height:16px}
  .v-review .kn-rc{margin:0 12px 14px;padding:14px}
  .v-review .cf-main,.v-review .cf-side{padding:20px}
  .v-review .cf-h{font-size:19px}
  .v-review .big b{font-size:40px}
  .v-review .cm summary{grid-template-columns:auto minmax(0,1fr) auto;padding:12px 14px}
  .v-review .cm .src{display:none}
  .v-review .cm .t{white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
  .v-review .cm .body{padding:4px 14px 14px}
  .v-review .pj>summary{grid-template-columns:minmax(0,1fr) auto;padding:14px 16px}
  .v-review .pj-sts{grid-column:1/-1;grid-row:2;justify-content:flex-start}
  .v-review .pj-body{padding:4px 16px 16px}
  .v-review .pj-cols{grid-template-columns:1fr}
  .v-review .about dl{grid-template-columns:1fr;gap:2px}
  .v-review .about dd{margin-bottom:8px}
}
"""

JS = """<script>(function(){
  // a cell of "What your agent knows" opens its receipt under the row; one receipt per row at a time
  document.querySelectorAll('.v-review .kn-c').forEach(function(b){
    b.addEventListener('click',function(){
      var row=b.closest('.kn-row'),open=b.getAttribute('aria-expanded')==='true';
      row.querySelectorAll('.kn-c').forEach(function(x){x.setAttribute('aria-expanded','false')});
      row.querySelectorAll('.kn-rc').forEach(function(x){x.hidden=true});
      if(!open){b.setAttribute('aria-expanded','true');document.getElementById(b.getAttribute('aria-controls')).hidden=false}
    });
  });
})();</script>"""


def _icon(name, cls=""):
    return _SVG.format(c=cls, d=ICON[name])


def _chev():
    return _icon("chev", "rv-chev")


def _text(ctx, s):
    """Escaped, with `code` spans: the reader writes file and command names in backticks."""
    return re.sub(r"`([^`\n]+)`", r"<code>\1</code>", ctx["esc"](str(s or "").strip()))


def _short(path):
    home = os.path.expanduser("~")
    path = str(path or "")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def _n(x):
    return f"{x:,}" if isinstance(x, int) else str(x)


def _clip(s, n=110):
    """One line of a longer sentence: its first sentence, else its first clause, else cut at a word, within n chars."""
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if len(s) <= n:
        return s
    first = re.split(r"(?<=[.!?])\s+(?=[A-Z])", s)[0]
    if len(first) <= n:
        return first.rstrip(".")
    cuts = [m.start() for m in re.finditer(r"[,;:]\s|\s[—–]\s", first) if 40 <= m.start() <= n]
    if cuts:
        return first[:cuts[-1]].rstrip(" ,;:") + "…"
    return first[:n].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def _name(p):
    return p.get("label") or p.get("slug") or "Your project"


def tagline(p):
    """A few words under a project's name. The reader's own `tagline` when it wrote one; else the words before the
    headline's first ":" (headlines often read "A tool-builder's setup: …"), else its first clause, at most 60 chars;
    else none."""
    own = re.sub(r"\s+", " ", str(p.get("tagline") or "")).strip().rstrip(".")
    if own:
        return " ".join(own.split()[:7])
    h = re.sub(r"\s+", " ", str(p.get("headline") or "")).strip()
    if not h:
        return ""
    if ":" in h and len(h.split(":", 1)[0].strip()) <= 60:
        return h.split(":", 1)[0].strip()
    clause = re.split(r";\s|\s[—–]\s|,\s(?=(?:where|which|with|while|and|but|so|that|whose|held|because)\b)|\.\s", h)[0]
    clause = clause.strip().rstrip(".")
    return clause if len(clause) <= 60 else ""


# ------------------------------------------------------------------ states

def _part(p, pid):
    """One project's part: its verdicts (`parts`) with what a verdict rests on (`read`), as the Your level tab reads it."""
    g = next((x for x in p.get("parts") or [] if isinstance(x, dict) and x.get("id") == pid), None)
    read = (p.get("read") or {}).get(pid) if isinstance(p.get("read"), dict) else None
    out = {**(g or {}), **(read if isinstance(read, dict) else {})}
    return out if g else None


def _state(p, pid):
    g = _part(p, pid)
    if not g:
        return "unread", ""
    return view_mylevel._part_state(g)


def _read_projects(projects):
    return [p for p in projects if any(isinstance(g, dict) and g.get("judged") not in (None, "", "can't tell")
                                       for g in p.get("parts") or [])]


def _store(ctx, p):
    """The project's memory store worth drawing (setup-map.json, setup_map.py): their own, with notes in it, the best
    shaped first. None when the map has no such store."""
    m = ctx.get("setup_map") if isinstance(ctx.get("setup_map"), dict) else {}
    want = p.get("slug") or p.get("label") or ""
    for mp in m.get("projects") or []:
        if not isinstance(mp, dict):
            continue
        base = os.path.basename(str(mp.get("root") or "").rstrip("/"))
        if re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") != want and base != p.get("label"):
            continue
        stores = [s for s in mp.get("memory") or [] if isinstance(s, dict) and s.get("shape") in SHAPE
                  and s.get("kind") != "builtin" and (s.get("files") or 0) > 0]
        if not stores:
            return None
        return max(stores, key=lambda s: (s.get("shape") in SHAPED, s.get("files") or 0))
    return None


def _shape_svg(shape, cls="shape"):
    words, d = SHAPE.get(shape, SHAPE["empty"])
    return (f'<svg class="{cls}" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" '
            f'stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="Memory shape: {words}">'
            f'<title>Memory shape: {words}</title>{d}</svg>')


def _st(state, word=None):
    cls, w = STATE[state]
    return f'<span class="st {cls}">{_icon(cls)}<span>{word or w}</span></span>'


# ------------------------------------------------------------------ 1. the level

def _mapped(ctx):
    """Every project the setup read mapped: the one list (things.mapped_projects) the Built tab's rule files walk too."""
    return level_things.mapped_projects(ctx.get("setup_map"), ctx.get("setup_read"))


def _noun(ctx):
    """"3 busiest projects" or "busiest project", or without "busiest" where a slot was filled another way."""
    m = _mapped(ctx)
    return level_things.projects_noun(m) if m else "busiest project"


def _and(xs):
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _why(lvl, pt, projects, noun="busiest project"):
    """One sentence, at most WHY_WORDS words, from the data: in how many projects each part shows (the Your level tab's
    rule), then what the level adds. "" when it would not fit."""
    read = _read_projects(projects)
    n, phrase = len(read), ""
    if n:
        k = {pid: sum(_state(p, pid)[0] == "shown" for p in read) for pid, _ in PARTS}
        groups = {}  # in how many projects -> the parts that show in that many
        for pid, _ in PARTS:
            groups.setdefault(k[pid], []).append(PART_WORD[pid])
        bits = []
        for cnt, names in sorted(groups.items(), reverse=True):
            if n == 1:
                where = f"show in your {noun}" if cnt else "not yet"
            elif not bits:
                where = (f"show in all {n} projects" if cnt == n else f"show in {cnt} of {n} projects" if cnt
                         else f"show in none of {n} projects")
            else:
                where = f"in {cnt}" if cnt else "in none"
            bits.append(f"{_and(names)} {where}")
        phrase = ", ".join(bits)
        phrase = phrase[:1].upper() + phrase[1:]
    tail = {"L4": "you build and test your own tools", "L2": "your own habits show",
            "L1": "you use Claude Code on real work"}.get(lvl, "")
    any_shown = n and any(_state(p, pid)[0] == "shown" for p in read for pid, _ in PARTS)
    if lvl in ("L1", "L2") and not any_shown:
        s = tail[:1].upper() + tail[1:] + "; your files are the next step."
    elif phrase:
        s = phrase + ("; " + tail if tail else "") + "."
        if len(s.split()) > WHY_WORDS:
            s = phrase + "."
    else:
        s = (tail[:1].upper() + tail[1:] + ".") if tail else ""
    return s if len(s.split()) <= WHY_WORDS else ""


def _hero(ctx, projects):
    """The level the Your level tab shows, as a mark and a four-step ladder; the ladder opens that tab. None on a page
    with no level tab, and none when the laptop shows no level."""
    if not ctx.get("level", True):
        return ""
    pt, which = level_things.active(ctx.get("v15_result") or {})
    lvl = (pt or {}).get("level")
    if lvl not in view_mylevel.MEANING:
        return ""
    esc = ctx["esc"]
    could = pt.get("could_be") if pt.get("could_be") in view_mylevel.MEANING and pt.get("could_be") != lvl else None
    met = level_things.things_met(pt) if which == "things" else {}
    why = _why(lvl, pt, projects, _noun(ctx))
    heights = {"L1": 34, "L2": 52, "L3": 70, "L4": 88}
    steps = []
    for k in sorted(view_mylevel.MEANING):
        have, of = met.get(k, (0, 0))
        unit = "thing"
        if k == "L3" and (pt.get("setup_read") or {}).get("parts"):
            # L3 is decided by the parts of the setup read, not by the six things: show what decides it (things.py parts_count)
            have, _, of = level_things.parts_count(pt["setup_read"]["parts"])
            unit = "part"
        cls = "you" if k == lvl else "below" if k < lvl else "could" if k == could else "above"
        dots = "".join(f'<i class="dot{" met" if j < have else ""}"></i>' for j in range(of))
        steps.append(f'<li class="{cls}" data-level="{k}" data-have="{have}" data-of="{of}" data-unit="{unit}" title="{k}: {esc(view_mylevel.MEANING[k])}"><span class="bar" style="--h:{heights[k]}px">{dots}</span>'
                     f'<b>{k}</b>' + (f'<small>{have} of {plural(of, unit)}</small>' if of else "") + "</li>")
    said = f"Your level: {lvl}, {view_mylevel.MEANING[lvl]}. See how it was read."
    return ('<section class="lv" aria-label="Your level">'
            f'<div class="lv-id"><div class="lv-mark" aria-hidden="true">{esc(lvl)}</div><div>'
            f'<p class="lv-over">Your level today' + (f'<span class="lv-could">Could be {esc(could)}</span>' if could else "") + "</p>"
            f'<h3 class="lv-name">{esc(view_mylevel.MEANING[lvl])}</h3>'
            + (f'<p class="lv-why">{esc(why)}</p>' if why else "") + "</div></div>"
            f'<a class="lad" href="#mylevel" aria-label="{esc(said)}"><ol>{"".join(steps)}</ol>'
            f'<span class="lad-foot"><span>{"In place, per level (L3 counts the parts of your setup)" if met else "The four levels"}</span>'
            f'<span class="rv-link">How it was read{_icon("arrow")}</span></span></a></section>')


# ------------------------------------------------------------------ 2. what your agent knows

def _quote(ctx, q, cap=None):
    where = _short(q.get("path")) + (f":{q['line']}" if q.get("line") else "")
    return (f'<figure class="q"><blockquote>{ctx["esc"](q.get("text") or "")}</blockquote>'
            + (f'<figcaption>{_icon("file")}{ctx["esc"](where)}</figcaption>' if where else "") + "</figure>")


def _receipt(ctx, i, p, pid, state, note, store):
    esc = ctx["esc"]
    g = _part(p, pid) or {}
    q = g.get("quote") if isinstance(g.get("quote"), dict) else {}
    out = [f'<div class="rc-top">{_st(state)}<b>{esc(PART_NAME[pid])}</b><span class="in">in {esc(_name(p))}</span></div>']
    if pid == "memory" and store:
        words, _ = SHAPE.get(store.get("shape"), SHAPE["empty"])
        files = store.get("files")
        out.append(f'<p class="rc-shape">{_shape_svg(store.get("shape"), "")}<span>{esc(words)}'
                   + (f' · {plural(files, "note")}' if isinstance(files, int) else "")
                   + f' · <code>{esc(store.get("path") or "")}</code></span></p>')
    # a quote the code did not find in the file is never shown: the verdict then rests on the sentence alone
    if g.get("quote_ok") and q.get("text"):
        out.append(_quote(ctx, q))
    elif state != "unread":
        out.append('<p class="rc-none">' + esc(note or "No line from your file backs this one yet.") + "</p>")
    if g.get("why"):
        out.append(f'<p class="rc-why">{_text(ctx, g["why"])}</p>')
    if state != "shown":
        nxt = g.get("next") or view_mylevel.NEXT.get((pid, "open")) or ""
        if state == "unread" and not g.get("next"):
            nxt = "Run the tool again so this project's files get read."
        if nxt:
            out.append(f'<p class="rc-next"><b>Next step</b>{_text(ctx, nxt)}</p>')
    return f'<div class="kn-rc" id="rc-{i}-{pid}" hidden>{"".join(out)}</div>'


def _knows(ctx, projects):
    esc = ctx["esc"]
    rows = []
    for i, p in enumerate(projects):
        cells, receipts = [], []
        store = _store(ctx, p)
        for pid, word in PARTS:
            state, note = _state(p, pid)
            shape = _shape_svg(store.get("shape")) if pid == "memory" and store else ""
            cells.append(f'<button type="button" class="kn-c" aria-expanded="false" aria-controls="rc-{i}-{pid}" '
                         f'aria-label="{word} in {esc(_name(p))}: {STATE[state][1]}. Show what it rests on">'
                         f'<span class="lab">{word}</span>{_st(state)}{shape}{_chev()}</button>')
            receipts.append(_receipt(ctx, i, p, pid, state, note, store))
        tag = tagline(p)
        rows.append(f'<div class="kn-row"><div class="kn-main"><div class="kn-name"><b>{esc(_name(p))}</b>'
                    + (f'<span title="{esc(tag)}">{esc(tag)}</span>' if tag else "") + "</div>"
                    + "".join(cells) + "</div>" + "".join(receipts) + "</div>")
    if not rows:
        return ""
    n, noun = len(projects), _noun(ctx)
    head = '<div class="kn-head" aria-hidden="true"><span>Project</span>' + "".join(f"<span>{w}</span>" for _, w in PARTS) + "</div>"
    return ('<section class="rv-sec"><h3>What your agent knows</h3>'
            f'<p class="rv-sub">Before you type a word, in {"each of your " + noun if n > 1 else "your " + noun}. '
            'Click a cell to see the line it rests on.</p>'
            f'<div class="kn">{head}{"".join(rows)}</div></section>')


# ------------------------------------------------------------------ 3. change this first

def _interleave(projects, key):
    """Items across projects, the first of each before the second of any: one busy project must not fill the screen."""
    lists = [[(p, x) for x in p.get(key) or [] if isinstance(x, dict)] for p in projects]
    out = []
    for i in range(max((len(x) for x in lists), default=0)):
        out += [x[i] for x in lists if i < len(x)]
    return out


def _changes(sr, projects):
    """Holds first (a habit in Claude Code's own records outweighs anything in the files), then the cards the code made
    from those records, then each project's changes, then a part's next step where all of those named too few."""
    items = []
    for h in sr.get("holds") or []:
        if not (isinstance(h, dict) and h.get("fired")):
            continue
        about_sessions = bool(re.search(r"compact|session", f"{h.get('id', '')} {h.get('why', '')}", re.I))
        items.append({"id": h.get("id") if about_sessions else "", "tag": HABIT_TAG, "saw": h.get("why") or h.get("id"),
                      "cost": h.get("cost") or (HOLD_COST if about_sessions else ""),
                      "do": h.get("do") or (HOLD_DO if about_sessions else "Bring it to your call: a habit changes "
                                            "faster with someone looking at it with you.")})
    # what the code counted in Claude Code's own records (setup-read.json "cards": how full a session starts, how long
    # sessions run, upkeep): numbers, so they come before the reader's opinions and after a habit that holds the level
    for c in sr.get("cards") or []:
        if isinstance(c, dict) and (c.get("saw") or c.get("do")):
            items.append({**c, "tag": RECORDS_TAG})
    many = len(projects) > 1
    for p, c in _interleave(projects, "change"):
        if c.get("saw") or c.get("do"):
            items.append({**c, "id": "", "tag": f"In {_name(p)}" if many else "", "project": _name(p)})
    if len(items) < SHOW:
        for g in sr.get("parts") or []:
            if isinstance(g, dict) and g.get("judged") in ("partly", "not shown") and g.get("next"):
                items.append({"id": "", "tag": g.get("name") or PART_NAME.get(g.get("id"), ""), "saw": g.get("why") or "",
                              "cost": "", "do": g["next"]})
    return items


def _num_in(text):
    """The number a sentence is about, with its unit ("23,900 words"): the first one followed by a unit word, never a
    line number. None when there is none."""
    for m in re.finditer(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?k?)\s+(?:[a-z-]+\s+)?(" + "|".join(UNITS) + r")\b", str(text or "")):
        before = text[max(0, m.start() - 6):m.start()].lower()
        if "line" in before or m.group(1) in ("0", "1"):
            continue
        return m.group(1), m.group(0)[len(m.group(1)):].strip()
    return None


def _pages(tokens):
    return max(1, round(tokens * WORDS_PER_TOKEN / WORDS_PER_PAGE))


def _figures(c):
    f = c.get("figures") if isinstance(c.get("figures"), dict) else {}
    saw = str(c.get("saw") or "")
    if c.get("id") == "starting-context" and not f:
        # a file written before `figures`: the code's own sentence ("about 44k tokens … about 4.1k of that")
        k = lambda s: round(float(s) * 1000)  # noqa: E731
        a = re.search(r"about ([\d.]+)k tokens", saw)
        b = re.search(r"about ([\d.]+)k of that", saw)
        if a:
            f = {"tokens": k(a.group(1)), "own_tokens": k(b.group(1)) if b else 0}
        m = re.search(r"median of ([\d,]+) sessions", saw)
        if m and f:
            f["sessions"] = int(m.group(1).replace(",", ""))
    if c.get("id") == "unused-setup" and not f:
        m = re.search(r"(\d+) of the (\d+) skills(?: and commands| or command)? you wrote were not loaded", saw)
        if m:
            f = {"own": int(m.group(2)), "own_used": int(m.group(2)) - int(m.group(1))}
    return f


def _picture(ctx, c):
    """The first change as a picture: (headline, picture html). By the card's id; a habit or a reader's change with no
    picture of its own shows the number its sentence is about, else nothing."""
    cid, f = c.get("id") or "", _figures(c)
    if cid == "starting-context" and f.get("tokens"):
        total, own = _pages(f["tokens"]), min(_pages(f["own_tokens"]) if f.get("own_tokens") else 0, _pages(f["tokens"]))
        shown = min(total, 240)
        cells = "".join(f'<i class="{"own" if j < own else ""}"></i>' for j in range(shown))
        k = f["tokens"] / 1000
        fine = (f'About {k:.0f}k tokens' + (f', the median of {plural(f["sessions"], "session")}' if f.get("sessions") else "")
                + f'. A page is {WORDS_PER_PAGE} words.')
        pic = (f'<div class="pages" role="img" aria-label="{plural(total, 'page')}, {own} of them your own rules">{cells}</div>'
               f'<div class="key"><span><i class="own"></i><span>Your own rules: <b>about {plural(own, 'page')}</b></span></span>'
               f'<span><i></i><span>Claude Code\'s instructions, tools and skills: <b>about {total - own}</b></span></span></div>'
               f'<p class="fine">{fine}' + (f' Showing the first {plural(shown, 'page')}.' if shown < total else "") + "</p>")
        return f"Your agent reads about {plural(total, 'page')} before you type a word", pic
    if cid == "unused-setup" and f.get("own"):
        own, used = int(f["own"]), int(f.get("own_used") or 0)
        shown = min(own, 300)
        dots = "".join(f'<i class="{"on" if j < used else ""}"></i>' for j in range(shown))
        pic = (f'<div class="dots" data-thing="skills-commands" data-count="{own}" data-used="{used}" role="img" aria-label="{plural(own, 'skill or command you wrote', 'skills and commands you wrote')}, {used} used in the last 30 days">{dots}</div>'
               f'<div class="key"><span><i class="dot on"></i><span>Used in the last 30 days: <b>{used}</b></span></span>'
               f'<span><i class="dot"></i><span>Not used: <b>{own - used}</b></span></span></div>')
        return f"Your agent picks from {plural(own, 'skill or command you wrote', 'skills and commands you wrote')}, and used {used} of them this month", pic
    if cid == "unused-setup" and f.get("others"):
        pic = (f'<div class="big" data-thing="use-others-tools" data-count="{f["others"]}" data-calls="{f.get("others_calls") or 0}">'
               f'<b>{_n(f["others"])}</b><span>skills, commands and plugins from others, called '
               f'{plural(f.get("others_calls") or 0, "time")} in 30 days</span></div>')
        return "Plugins and skills you don't use still sit in every session", pic
    head = (CARD_SAYS.get(cid) or {}).get("head") or _clip(c.get("cost") or c.get("saw") or c.get("do"), 120)
    num = _num_in(c.get("saw")) or _num_in(c.get("cost"))
    pic = f'<div class="big"><b>{ctx["esc"](num[0])}</b><span>{ctx["esc"](num[1])}</span></div>' if num else ""
    return head, pic


def _steps(ctx, c):
    """("Do this", "Why it pays") in one line each: the card's own words where they are short, else the plain ones."""
    says = CARD_SAYS.get(c.get("id") or "") or {}
    f = _figures(c)
    do = says.get("do")
    if c.get("id") == "starting-context" and f.get("tokens") and (f.get("own_tokens") or 0) / f["tokens"] >= 0.3:
        do = says["do_rules"]
    if c.get("id") == "unused-setup" and f.get("others") and not f.get("own"):
        do = says["do_others"]
    return do or _clip(c.get("do"), 120), says.get("pays") or ""


def _full(ctx, c, skip_cost=False):
    """What we saw, what it costs, the whole step: the card's own sentences, behind a click."""
    return "".join(f'<p class="{k}"><span class="lbl">{label}</span>{_text(ctx, c[k])}</p>'
                   for k, label in (("saw", "What we saw"), ("cost", "What it costs you"), ("do", "Do this"))
                   if c.get(k) and not (skip_cost and k == "cost"))


def _first(ctx, c):
    esc = ctx["esc"]
    head, pic = _picture(ctx, c)
    do, pays = _steps(ctx, c)
    side = ((f'<div class="do"><span class="lbl">Do this</span><p>{_text(ctx, do)}</p></div>' if do else "")
            + (f'<div class="pays"><span class="lbl">Why it pays</span><p>{esc(pays)}</p></div>' if pays else
               f'<div class="pays"><span class="lbl">What it costs you</span><p>{_text(ctx, _clip(c.get("cost"), 140))}</p></div>'
               if c.get("cost") else "")
            + f'<details class="cf-more"><summary>{_chev()}The whole story</summary><div class="cf-full">{_full(ctx, c)}</div></details>')
    return ('<article class="cf"><div class="cf-main">'
            + (f'<span class="cf-tag">{esc(c["tag"])}</span>' if c.get("tag") else "")
            + f'<h4 class="cf-h">{_text(ctx, head)}</h4>{pic}</div><div class="cf-side">{side}</div></article>')


def _line(ctx, i, c):
    """One more change: one line, opening on what we saw, what it costs and the step."""
    title = (CARD_SAYS.get(c.get("id") or "") or {}).get("head") or (
        _picture(ctx, c)[0] if c.get("id") in ("starting-context", "unused-setup") else _clip(c.get("cost") or c.get("saw") or c.get("do"), 140))
    src = c.get("project") or ("Claude Code's records" if c.get("tag") in (RECORDS_TAG, HABIT_TAG) else c.get("tag") or "")
    return (f'<li><details><summary><span class="n">{i}</span><span class="t" title="{ctx["esc"](title)}">{_text(ctx, title)}</span>'
            + (f'<span class="src">{ctx["esc"](src)}</span>' if src else "") + f'{_chev()}</summary>'
            f'<div class="body">{_full(ctx, c)}</div></details></li>')


def _change(ctx, sr, projects):
    items = _changes(sr, projects)
    if not items:
        return ('<section class="rv-sec"><h3>Change this first</h3><p class="none-yet">Nothing stood out to change. '
                'What you\'ve built lists each part of your setup.</p></section>')
    # the main card and at most MORE_SHOWN lines (11-11: ten lines here repeated what each project's section lists). A
    # project's other changes stay in its row under "Each project"; anything that belongs to no project opens here.
    rest, more = items[1:], ""
    shown, left = rest[:MORE_SHOWN], rest[MORE_SHOWN:]
    if shown:
        loose = [c for c in left if not c.get("project")]
        in_projects = len(left) - len(loose)
        more = (f'<div class="cm"><h4>{len(shown)} more to change</h4>'
                f'<ul>{"".join(_line(ctx, i + 2, c) for i, c in enumerate(shown))}</ul>'
                + (f'<details class="cm-rest"><summary>{_chev()}Show {len(loose)} more from Claude Code\'s records</summary>'
                   f'<ul>{"".join(_line(ctx, i + 2 + len(shown), c) for i, c in enumerate(loose))}</ul></details>' if loose else "")
                + (f'<p class="cm-left">{in_projects} more, each under its project <a href="#review-projects">further down this page</a>.</p>'
                   if in_projects else "") + "</div>")
    return ('<section class="rv-sec"><h3>Change this first</h3>'
            '<p class="rv-sub">Where a small change pays back most. The rest are in order below it.</p>'
            + _first(ctx, items[0]) + more + "</section>")


# ------------------------------------------------------------------ 4. what works

def _works(ctx, projects):
    """Receipts: what works, and the line from their file that shows it. The ones the code found a line for first."""
    esc, many = ctx["esc"], len(projects) > 1
    found = [(p, w) for p, w in _interleave(projects, "works") if w.get("what") or w.get("why")]
    found.sort(key=lambda pw: not (pw[1].get("quote_ok") and isinstance(pw[1].get("quote"), dict) and pw[1]["quote"].get("text")))
    cards = []
    for p, w in found:
        q = w.get("quote") if isinstance(w.get("quote"), dict) else {}
        # a quote the code did not find in the file is not shown: the card then rests on the sentence alone
        quote = _quote(ctx, q) if w.get("quote_ok") and q.get("text") else ""
        cards.append('<article class="wk"><div class="wk-top">'
                     f'<span class="wk-ok">{_icon("shown")}Works</span>'
                     + (f'<span class="wk-in">{esc(_name(p))}</span>' if many else "") + "</div>"
                     f'<h4>{_text(ctx, w.get("what") or _clip(w.get("why"), 120))}</h4>{quote}'
                     + (f'<details><summary>{_chev()}Why it matters</summary><p>{_text(ctx, w["why"])}</p></details>' if w.get("why") else "")
                     + "</article>")
    return cards


def _works_section(ctx, projects):
    cards = _works(ctx, projects)
    if not cards:
        body = ('<p class="none-yet">The read found nothing it could point to with a line from your files yet. '
                'The changes above are where to start.</p>')
    else:
        body = f'<div class="wk-grid">{"".join(cards[:SHOW])}</div>' + (
            f'<details class="wk-more"><summary>{_chev()}Show all {len(cards)}</summary>'
            f'<div class="wk-grid">{"".join(cards[SHOW:])}</div></details>' if len(cards) > SHOW else "")
    return ('<section class="rv-sec"><h3>What works</h3>'
            '<p class="rv-sub">Each with the line from your own files that shows it, found there by code.</p>' + body + "</section>")


# ------------------------------------------------------------------ 5. each project, and its outline as a file tree

def _parse_map(md):
    """The outline (setup_map.py's setup-map.md), read leniently since it is another step's text, into blocks:
    {title, note, sections: [{name, kind, items: [{path, words, extra, line}], more}]}. A markdown heading at the left
    edge opens a section ("## Project: …" a block), a bullet at the left edge is an item, an arrow under a file is a
    pointer to another file (an on-demand file of its own), "… N more" is a count of what the map left out."""
    blocks, block, sec, last = [], None, None, None

    def new_block(title=None):
        b = {"title": title, "note": None, "sections": []}
        blocks.append(b)
        return b

    for raw in str(md or "").splitlines():
        if not raw.strip():
            continue
        line = raw.expandtabs(2)
        depth = (len(line) - len(line.lstrip(" "))) // 2
        s = line.strip()
        bullet = bool(re.match(r"[-*+]\s+", s))
        s = re.sub(r"^[-*+]\s+", "", s)
        top = depth == 0
        if top and s.startswith("#"):
            name = s.lstrip("#").strip()
            m = re.match(r"(?:Project|User)\b[^:]*:\s*(.+)", name)
            if m or name.lower().startswith("user"):
                block, sec = new_block(m.group(1) if m else name), None
                continue
            block = block or new_block()
            kind = ("always" if "always" in name.lower() else "scoped" if re.search(r"scoped|folder", name, re.I) else
                    "demand" if "demand" in name.lower() else "memory" if "memory" in name.lower() else
                    "skills" if "skill" in name.lower() else "agents" if re.search(r"agent|command", name, re.I) else "other")
            sec = {"name": name, "kind": kind, "items": [], "more": 0}
            block["sections"].append(sec)
            continue
        if top and s.endswith(":") and not bullet:
            block = block or new_block()
            sec = {"name": s[:-1], "kind": "other", "items": [], "more": 0}
            block["sections"].append(sec)
            continue
        block = block or new_block()
        if top and not bullet and len(s.split()) > 6 and sec is None:
            block["note"] = s
            continue
        if s.startswith("…"):
            m = re.search(r"(\d[\d,]*)", s)
            if top and sec is not None and m:
                sec["more"] += int(m.group(1).replace(",", ""))
            continue
        if sec is None:
            sec = {"name": "", "kind": "other", "items": [], "more": 0}
            block["sections"].append(sec)
        if s.startswith(("→", "->")):
            # a pointer under a file: the file it points to is one the agent opens on demand
            t = s.lstrip("→->").strip()
            m = re.match(r"(\S+)(?:\s+\(([\d,]+)\s*w(?:ords)?\))?", t)
            if m and last is not None:
                last.setdefault("points", []).append({"path": m.group(1), "words": m.group(2), "from": last["path"]})
            continue
        if not top:
            continue  # a heading inside a file, an index line: the tree draws files, the reader's prose has the rest
        if s.lower().startswith("not listed"):
            continue
        m = re.match(r"(?P<path>.+?)\s+\(?(?P<w>[\d,]+)\s*w(?:ords)?\)?(?=[\s,]|$)(?P<rest>.*)$", s)
        item = ({"path": m.group("path").strip(), "words": m.group("w"), "extra": m.group("rest").strip(" ,"), "line": s}
                if m else {"path": s.split()[0] if s.split() else s, "words": None, "extra": "", "line": s})
        sec["items"].append(item)
        last = item
    return blocks


def _dir_tree(entries):
    root = {"dirs": {}, "files": []}
    for path, kind, it in entries:
        bits = [b for b in path.split("/") if b]
        if not bits:
            continue
        node = root
        for d in bits[:-1]:
            node = node["dirs"].setdefault(d, {"dirs": {}, "files": []})
        node["files"].append((bits[-1], kind, it))
    return root


def _draw(ctx, node):
    esc, rows = ctx["esc"], []
    for name, kind, it in node["files"]:
        label, cls = LOADS[kind]
        w = it.get("words")
        frm = it.get("from")
        title = f' title="{esc("opened from " + frm)}"' if frm else ""
        rows.append(f'<li><div class="ft-r"{title}>{_icon("file")}<span>{esc(name)}</span>'
                    + (f'<span class="w">{esc(w)} words</span>' if w else "") + f'<span class="ld {cls}">{label}</span></div></li>')
    for name, sub in node["dirs"].items():
        path = name
        while not sub["files"] and len(sub["dirs"]) == 1:   # a folder holding one folder reads as one line
            (nxt, sub), = sub["dirs"].items()
            path += "/" + nxt
        rows.append(f'<li><div class="ft-r dir">{_icon("folder")}<span>{esc(path)}/</span></div>{_draw(ctx, sub)}</li>')
    return f"<ul>{''.join(rows)}</ul>" if rows else ""


def tree(ctx, md):
    """The outline as a picture a person reads at a glance: the files the agent reads as a folder tree, each with its
    size and when it loads (always, in its folder, on demand); then the memory with its shape, the skills and the
    agents by name. Each project of a whole map gets its own tree."""
    esc, out = ctx["esc"], []
    blocks = _parse_map(md)
    for b in blocks:
        entries, seen, more, mem, chips = [], set(), [], [], []
        for sec in b["sections"]:
            if sec["kind"] in LOADS:
                for it in sec["items"]:
                    if it["path"] not in seen:
                        seen.add(it["path"])
                        entries.append((it["path"], sec["kind"], it))
                if sec["more"]:
                    more.append(f'{_n(sec["more"])} more {LOADS[sec["kind"]][0].lower()}')
            elif sec["kind"] == "memory":
                for it in sec["items"]:
                    shape = next((s for s in SHAPE if re.search(rf"\b{re.escape(s)}\b", it["line"])), None)
                    if shape == "empty" or "(builtin)" in it["line"]:
                        continue
                    m = re.search(r"(\d[\d,]*)\s+(?:files|notes)", it["line"])
                    words = SHAPE[shape][0] if shape else ""
                    path = re.split(r"\s+(?:\(|—|-\s)", it["line"], 1)[0].strip()
                    count = (f' · {esc(plural(int(m.group(1).replace(",", "")), "note"))}'
                             if m and shape != "single-file" else "")
                    mem.append(f'<div>{_shape_svg(shape) if shape else ""}<code>{esc(path)}</code>'
                               f'<span>{esc(words)}{count}</span></div>')
            elif sec["kind"] in ("skills", "agents") and sec["items"]:
                names = [it["path"] for it in sec["items"]]
                chips.append(f'<div class="ft-chips"><b>{"Skills" if sec["kind"] == "skills" else "Agents and commands"}</b>'
                             + "".join(f"<span>{esc(n)}</span>" for n in names)
                             + (f'<em class="fine">+ {_n(sec["more"])} more</em>' if sec["more"] else "") + "</div>")
        # files the rule files point to load on demand: drawn where they sit, once. A skill's or an agent's pointer is
        # relative to its own folder, which the outline doesn't give, so only rule files' pointers are drawn
        for sec in b["sections"]:
            for it in sec["items"] if sec["kind"] in LOADS else []:
                for ptr in it.get("points") or []:
                    if ptr["path"] not in seen and not ptr["path"].startswith(("http:", "https:")):
                        seen.add(ptr["path"])
                        entries.append((ptr["path"], "demand", ptr))
        if len(entries) > TREE_FILES:
            more.insert(0, f"{len(entries) - TREE_FILES} more files")
            entries = entries[:TREE_FILES]
        # always-loaded first, then folder files, then on demand: the order the agent meets them
        order = {"always": 0, "scoped": 1, "demand": 2}
        entries.sort(key=lambda e: order[e[1]])
        body = _draw(ctx, _dir_tree(entries))
        if not (body or mem or chips):
            continue
        out.append((f'<p class="ft-title">{esc(b["title"])}</p>' if b["title"] and len(blocks) > 1 else "")
                   + body + "".join(f'<p class="ft-more">+ {esc(m)}</p>' for m in more)
                   + (f'<div class="ft-mem">{"".join(mem)}</div>' if mem else "") + "".join(chips))
    return f'<div class="ft">{"".join(out)}</div>' if out else ""


def _project(ctx, p):
    esc = ctx["esc"]
    sts = "".join(f'<span class="st {STATE[_state(p, pid)[0]][0]}" title="{w}: {STATE[_state(p, pid)[0]][1]}">'
                  f'{_icon(STATE[_state(p, pid)[0]][0])}<span>{w}</span><span class="rv-sr">: {STATE[_state(p, pid)[0]][1]}</span></span>'
                  for pid, w in PARTS)
    tag = tagline(p)
    works = [w for w in p.get("works") or [] if isinstance(w, dict) and w.get("what")]
    changes = [c for c in p.get("change") or [] if isinstance(c, dict) and (c.get("do") or c.get("saw"))]
    cols = ""
    if works or changes:
        cols = ('<div class="pj-cols">'
                + (('<section><h5>What works here</h5><ul>' + "".join(f'<li class="ok">{_icon("shown")}<span>{_text(ctx, w["what"])}</span></li>' for w in works)
                    + "</ul></section>") if works else "<section></section>")
                + (('<section><h5>What to change here</h5><ul>' + "".join(f'<li class="to">{_icon("arrow")}<span>{_text(ctx, c.get("do") or c.get("saw"))}</span></li>' for c in changes)
                    + "</ul></section>") if changes else "<section></section>") + "</div>")
    t = tree(ctx, p.get("map_md"))
    body = ((f'<p class="pj-hl">{_text(ctx, p["headline"])}</p>' if p.get("headline") else "")
            + (f'<p class="pj-sum">{_text(ctx, p["summary"])}</p>' if p.get("summary") else ""))
    return (f'<details class="pj"><summary><span class="kn-name"><b>{esc(_name(p))}</b>'
            + (f'<span>{esc(tag)}</span>' if tag else "") + f'</span><span class="pj-sts">{sts}</span>{_chev()}</summary>'
            f'<div class="pj-body">' + (f"<div>{body}</div>" if body else "") + cols
            + (f"<div><h5>How it is laid out</h5>{t}</div>" if t else "") + "</div></details>")


def _projects(ctx, projects):
    if not projects:
        return ""
    return ('<section class="rv-sec" id="review-projects"><h3>Each project</h3>'
            '<p class="rv-sub">The review of each one, and how its files are laid out.</p>'
            f'<div class="pj-list">{"".join(_project(ctx, p) for p in projects)}</div></section>')


# ------------------------------------------------------------------ the rest

def _outline_without_a_read(ctx, projects):
    """What the code mapped, when no agent read it: each project's outline, or the run's whole map file."""
    trees = [(_name(p), tree(ctx, p.get("map_md"))) for p in projects]
    trees = [(n, t) for n, t in trees if t]
    if not trees:
        md = ctx.get("setup_map_md")
        path = os.path.join(ctx.get("run") or "", "setup-map.md")
        if md is None and ctx.get("run") and os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                md = f.read()
        t = tree(ctx, md)
        trees = [("", t)] if t else []
    return "".join((f'<p class="ft-title">{ctx["esc"](n)}</p>' if n else "") + t for n, t in trees)


def _about(ctx):
    """Who the page was read for, the window, Claude Code and its log setting, and how this copy was built: once, at
    the bottom (frame.build puts it in ctx["about"]; the header keeps only the title and the window)."""
    a = ctx.get("about") if isinstance(ctx.get("about"), dict) else {}
    esc = ctx["esc"]
    rows = [("Read for", a.get("who")),
            ("Period", f'Last {plural(a.get("days", 30), "day")}, to {a["to"]}' if a.get("to") else None),
            ("This laptop", a.get("laptop")),
            ("How it was made", "Code mapped the files your agent loads. An agent then read them the way a colleague "
                                "would, and code checked every quoted line against your file. Nothing left this laptop."),
            ("This copy", ("Experimental. " + a["stamp"][:1].upper() + a["stamp"][1:]) if a.get("stamp") else "Experimental.")]
    return ('<details class="about"><summary>' + _chev() + 'About this report</summary><dl>'
            + "".join(f"<dt>{k}</dt><dd>{esc(v)}</dd>" for k, v in rows if v) + "</dl></details>")


def render(ctx):
    sr = ctx.get("setup_read")
    sr = sr if isinstance(sr, dict) else {}
    projects = [e["read"] for e in _mapped(ctx) if e["read"]]
    out = [_hero(ctx, projects)]
    if not sr.get("ran"):
        outline = _outline_without_a_read(ctx, projects)
        out.append('<section class="rv-sec"><div class="rv-empty"><b>The read of your files did not finish on this run, '
                   'so there is no review yet.</b><p>Run the tool again to get it.'
                   + (" Below is how your files are laid out, which this run did map." if outline else "") + "</p></div>"
                   + (f'<div style="margin-top:16px">{outline}</div>' if outline else "") + "</section>")
        return "".join(out) + _about(ctx)
    out += [_knows(ctx, projects), _change(ctx, sr, projects), _works_section(ctx, projects), _projects(ctx, projects),
            _about(ctx), JS]
    return "".join(out)
