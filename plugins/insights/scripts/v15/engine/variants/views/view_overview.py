"""Your setup at a glance: a one-sentence portrait, how one task moves through the setup, and what the person built.

Reads inventory.json, anatomy.json, cheap-artifacts.json, cheap-problems.json and v15-result.json. Everything here is
counted or parsed by code; the receipts name the file, the field and the rule. Stdlib only.
"""
import os
import re
import check_kit  # noqa: E402
import commit_kit  # noqa: E402
import panel_kit as PK  # noqa: E402  a name opens the shared side panel

ORDER = 10
TITLE = "Your setup at a glance"
QUESTION = "How you work in one look, and the path one task takes through your setup."
PROXIFY_ONLY = True  # member copy: off (11-06, a tester's "the first look on the report feels overwhelming"; By discipline carries the verdict and the step)

CSS = """
.v-overview .lede{font-size:18px;font-weight:600;line-height:1.45;letter-spacing:-.005em;margin:0 0 8px;max-width:62ch;text-wrap:balance}
.v-overview .facts{color:#475467;margin:8px 0 0;max-width:72ch}
.v-overview .morefacts{margin:0 0 32px}.v-overview .morefacts>summary{font-size:13px;color:#3C3ED4}
.v-overview .split{display:grid;grid-template-columns:1fr 1fr;gap:16px 48px;margin:0 0 32px}
.v-overview h3{font-size:14px;font-weight:600;margin:0 0 8px;display:flex;align-items:baseline;gap:8px}
.v-overview h3 .c{font-size:12px;font-weight:500;color:#667085}
.v-overview .list{list-style:none;margin:0;padding:0}
.v-overview .list li{border-top:1px solid #EAECF0}
.v-overview .list li:last-child{border-bottom:1px solid #EAECF0}
.v-overview .list summary{list-style:none;display:flex;gap:8px;align-items:flex-start;padding:8px 0;color:#15172F}
.v-overview .list summary::-webkit-details-marker{display:none}
.v-overview .list summary svg{flex:none;width:16px;height:16px;margin-top:3px}
.v-overview .list summary b{font-weight:500}
.v-overview .list summary .def{display:block;font-size:12px;color:#667085;line-height:1.4}
.v-overview .list.good svg{color:#039855}.v-overview .list.weak svg{color:#DC6803}
.v-overview .list summary:hover{color:#3C3ED4}
.v-overview .list .why{margin:0 0 16px 24px;font-size:12px;color:#475467}
.v-overview .list .why>p{margin:0;max-width:70ch}
.v-overview .list .more>summary{color:#3C3ED4;font-size:12px;font-weight:500}
.v-overview table.proj{width:100%;border-collapse:collapse;margin:0 0 8px}
.v-overview table.proj th{text-align:right;font-size:12px;font-weight:500;color:#667085;padding:0 0 8px;border-bottom:1px solid #EAECF0}
.v-overview table.proj th:first-child,.v-overview table.proj td:first-child{text-align:left}
.v-overview table.proj td{text-align:right;padding:8px 0;border-bottom:1px solid #EAECF0}
.v-overview table.proj td:first-child{font-weight:500}
.v-overview .how{margin:12px 0 0;font-size:12px;color:#475467}
.v-overview .how>summary{color:#3C3ED4;font-weight:500;width:max-content}
.v-overview .how dl{margin:8px 0 0;display:grid;grid-template-columns:96px 1fr;gap:4px 16px;padding:12px 16px;background:#FFFFFF;border:1px solid #EAECF0;border-radius:8px}
.v-overview .how dt{font-weight:600;color:#15172F}
.v-overview .how dd{margin:0;overflow-wrap:anywhere}
.v-overview .how ul{margin:0;padding-left:16px}
.v-overview h4{font-size:18px;font-weight:600;margin:56px 0 4px}
.v-overview .sub{color:#475467;margin:0 0 24px;max-width:72ch}
.v-overview .flow{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(6,minmax(0,1fr))}
.v-overview .stage{min-width:0;padding-right:16px}
.v-overview .node{position:relative;height:16px;margin:0 0 12px}
.v-overview .node:before{content:"";position:absolute;left:0;right:0;top:7px;height:2px;background:#EAECF0}
.v-overview .stage:last-child .node:before{right:auto;width:16px}
.v-overview .node i{position:absolute;left:0;top:2px;width:12px;height:12px;border-radius:999px;background:#F9FAFB;border:2px solid #949BAD}
.v-overview .stage.you .node i{background:#5258FB;border-color:#5258FB;box-shadow:0 0 0 4px #E5E8F9}
.v-overview .stage h5{font-size:14px;font-weight:600;line-height:1.35;margin:0}
.v-overview .youtag{font-size:12px;font-weight:600;color:#3C3ED4;margin:2px 0 0}
.v-overview .who{font-size:12px;color:#667085;margin:2px 0 12px;line-height:1.5}
.v-overview .part{padding:6px 0;border-top:1px solid #EAECF0;font-size:12px;min-width:0}
.v-overview .part>summary{list-style:none;color:#15172F;font-weight:500;overflow-wrap:anywhere;line-height:1.45}
.v-overview .part>summary::-webkit-details-marker{display:none}
.v-overview .part>summary:hover{color:#3C3ED4}
.v-overview .part small{display:block;color:#667085;font-size:12px;font-weight:400}
.v-overview .part pre{white-space:pre-wrap;overflow-wrap:anywhere;font:400 12px/1.5 Inter,system-ui,sans-serif;color:#475467;margin:4px 0;max-height:200px;overflow:auto}
.v-overview .more>summary{list-style:none;font-size:12px;font-weight:500;color:#3C3ED4;padding:6px 0;border-top:1px solid #EAECF0}
.v-overview .more>summary::-webkit-details-marker{display:none}
.v-overview .none{font-size:12px;color:#667085;margin:0}
.v-overview .fams{border-top:1px solid #EAECF0}
.v-overview .fam{border-bottom:1px solid #EAECF0}
.v-overview .fam>summary{list-style:none;display:grid;grid-template-columns:minmax(0,1.3fr) 48px minmax(0,1.2fr) minmax(0,1.6fr) 16px;gap:16px;align-items:baseline;padding:12px 0}
.v-overview .fam>summary::-webkit-details-marker{display:none}
.v-overview .fam>summary:hover .nm b{color:#3C3ED4}
.v-overview .fam .nm b{display:block;font-weight:600}
.v-overview .fam .nm small{display:block;font-size:12px;color:#667085}
.v-overview .fam .num{font-size:18px;font-weight:600;text-align:right}
.v-overview .fam .st,.v-overview .fam .top{font-size:12px;color:#475467}
.v-overview .fam .top{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.v-overview .chev{width:8px;height:8px;border-right:1.5px solid #949BAD;border-bottom:1.5px solid #949BAD;transform:rotate(-45deg);justify-self:end;align-self:center;transition:transform .15s}
.v-overview .fam[open] .chev{transform:rotate(45deg)}
.v-overview .fam .inner{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0 24px;padding:0 0 16px}
.v-overview .empty{color:#667085}
@media (max-width:900px){.v-overview .split{grid-template-columns:1fr}
  .v-overview .flow{grid-template-columns:repeat(2,minmax(0,1fr));row-gap:24px}
  .v-overview .fam>summary{grid-template-columns:minmax(0,1fr) 40px 16px}.v-overview .fam .st,.v-overview .fam .top{display:none}
  .v-overview .fam .inner{grid-template-columns:1fr}}
"""

ICON_OK = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
           'stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="6.25"/><path d="m5.5 8.2 1.7 1.7 3.3-3.6"/></svg>')
ICON_WARN = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
             'stroke-linejoin="round" aria-hidden="true"><path d="M8 2.2 14.3 13.3H1.7z"/><path d="M8 6.5v3M8 11.6v.1"/></svg>')

# short plain names for the practices and problems this run knows; anything else falls back to a trimmed full name
SHORT = {
    "write-down-what-it-cannot-know": "Writes down what the agent can't know",
    "put-rules-where-they-apply": "Rules load where they apply",
    "let-the-agent-fetch": "Agent fetches what it needs",
    "notes-the-next-chat-reads": "Notes the next session reads",
    "agent-managed-memory": "Agent-kept memory store",
    "bound-the-task": "One bounded task, a finish line",
    "prove-it-works": "Agent shows it works first",
    "retest-agent-setup": "Setup re-tested with evals",
    "enforce-fixed-rules-in-code": "Fixed rules enforced by checks",
    "give-the-agent-tools-made-for-it": "Tools made for the agent",
    "step-in-on-signals": "Steps in only on signals",
    "runs-start-without-you": "Runs start without you",
    "model-and-budget-per-step": "Model and spend per step",
}
# a problem's name on the page = its industry term (his 11-04: "use the industry terms"); the substrate's sentence
# follows it as the one-line definition. The challenge id is the term, except where the casing differs.
TERM = {"yolo-mode": "YOLO mode", "ai-slop": "AI slop", "seventy-percent-problem": "70% problem"}


def _term(i):
    return TERM.get(i) or i.replace("-", " ").capitalize()


def _short(i, names):
    if i in SHORT:
        return SHORT[i]
    n = names.get(i) or i.replace("-", " ")
    return n if len(n) <= 40 else n[:39].rstrip() + "…"


def _n(x):
    return f"{int(x):,}"


def _how(esc, rows):
    """A receipt: rows = [(label, text-or-list)]. Plain words: where, rule, who decided, whether checked."""
    out = ""
    for k, v in rows:
        if isinstance(v, (list, tuple)):
            v = "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in v) + "</ul>"
        else:
            v = esc(v)
        out += f"<dt>{esc(k)}</dt><dd>{v}</dd>"
    return f'<details class="how"><summary>How we know</summary><dl>{out}</dl></details>'


CODE = "Code counted it; no AI read decided it."
UNCHECKED = "Not checked by a person yet."

_HOOK = re.compile(r"^(?P<ev>[^·]+?) · (?P<src>.+?) \((?P<auth>theirs (?P<a>\d+)/(?P<b>\d+)|authorship unknown)\)"
                   r"(?: · (?P<runs>\d+)/(?P<of>\d+) (?P<unit>typed (?:chats|sessions)|headless runs))?")
_THEIRS = re.compile(r"theirs (\d+)/(\d+)")
_EVRUN = re.compile(r"(\d+)/(\d+) runs")
_HELP = re.compile(r"(?:helper|sub-agent) calls (\d+)/(\d+)ch")  # the scan may rename "helper" (his 11-04 call)
_ONOFF = re.compile(r"@\S+ (on|off)\b")


def _parse(a, anat_origin):
    """One artifact -> dict(label, source, runs, unit, own) where own is True/False/None (None = not marked)."""
    k, i, t = a["kind"], a["id"], a.get("text") or ""
    d = {"kind": k, "label": i, "source": "", "runs": None, "unit": "", "own": None, "text": t, "event": "", "on": None}
    if k == "hook":
        m = _HOOK.match(t)
        script = i.rsplit(":", 1)[-1]
        d["label"] = script[:44] + ("…" if len(script) > 44 else "")
        if "(not in any settings" in i and not re.fullmatch(r"[\w.\-/]+", script.strip()):
            # the scanner names a hook it saw in the logs but no longer finds set up by its command or prompt text;
            # that text is not a name. Say what it is; the raw text stays in the part's body.
            d["label"] = "A hook no longer set up"
        if m:
            d["event"] = m["ev"].strip()
            src = m["src"]
            d["source"] = src.split("@")[0] if "@" in src or ":" not in src else ("project settings" if src.startswith("project") else "home settings" if src.startswith("home") else "not in a settings file read now")
            if "(not in any" in src or src.startswith("(not in"):
                d["source"] = "not in a settings file read now"
            if m["a"]:
                d["own"] = int(m["a"]) * 2 >= int(m["b"]) > 0
            if m["runs"]:
                d["runs"], d["unit"] = int(m["runs"]), m["unit"].replace("chats", "sessions")
        else:
            d["event"] = t.split(" · ")[0]
    elif k == "agent":
        d["label"] = i.split(":", 1)[-1]
        m = _HELP.search(t)
        if m:
            d["runs"], d["unit"] = int(m[1]), "sub-agent calls"
        o = anat_origin.get(d["label"])
        d["own"] = None if o in (None, "untracked") else o == "own"
    elif k == "eval":
        p = i.split(":", 1)[-1].rstrip("/").split("/")
        d["label"] = "/".join(p[-3:-1]) if len(p) >= 3 else p[-1]
        m = _EVRUN.search(t)
        if m:
            d["runs"], d["unit"] = int(m[1]), "runs"
        m = _THEIRS.search(t)
        if m:
            d["own"] = int(m[1]) * 2 >= int(m[2]) > 0
    elif k == "plugin":
        d["label"] = i.split(":", 1)[-1]
        m = _ONOFF.search(t)
        d["on"] = (m[1] == "on") if m else None
        m = _THEIRS.search(t)
        if m:
            d["own"] = int(m[1]) * 2 >= int(m[2]) > 0
    elif k in ("runner", "test", "tool"):
        d["label"] = i.split(":", 1)[-1].rstrip("/").rsplit("/", 1)[-1]
        d["source"] = "/".join(i.split(":", 1)[-1].replace("~/", "").split("/")[:-1][-2:]) if "/" in i else ""
    elif k == "launch":
        d["label"] = i.split(":", 1)[-1]
    return d


def _setup_items(inv, arts):
    """What they built and customised that cheap-artifacts.json doesn't list, read straight from inventory.json:
    their own skills, commands, workflows, output styles and marketplaces (git says they wrote them), the MCP servers
    set up, each settings file with values set, and eval folders the digest skipped for having no runs or cases.
    Without this the "What you built" shelves showed 7 of the scan's kinds and hid 118 skills (his 11-04 review)."""
    home = inv["meta"].get("home") or os.path.expanduser("~")
    short = lambda p: (p or "").replace(home, "~", 1)  # noqa: E731
    out = []
    used = {}
    for r in inv.get("skills_used") or []:
        used[r["what"].split(":")[-1]] = used.get(r["what"].split(":")[-1], 0) + r["calls"]
    typed = {c["command"].lstrip("/"): c["times"] for c in (inv.get("slash_commands") or {}).get("by_command", [])}
    active = {}
    for s in inv.get("settings") or []:
        v = (s.get("notable") or {}).get("outputStyle")
        if v:
            active.setdefault(str(v).lower(), []).append(short(s["path"]))
    kinds = {"skill": "skill", "command": "command", "workflow": "workflow", "output_style": "output_style",
             "marketplace_manifest": "marketplace"}
    seen = {}
    for a in (inv.get("own_artifacts") or {}).get("own", []):
        k = kinds.get(a["kind"])
        if not k:
            continue
        key = (k, a["name"])
        if key in seen:  # the same command copied into many folders is one thing, with more copies
            seen[key]["copies"] += 1
            continue
        d = {"kind": k, "label": a["name"], "source": "/".join(short(os.path.dirname(a["path"])).split("/")[-2:]),
             "runs": None, "unit": "", "own": True, "event": "", "on": None, "copies": 1,
             "text": " · ".join(x for x in (a.get("description") or "", short(a["path"]),
                                            f"{(a.get('git') or {}).get('user_commits', '?')} of {(a.get('git') or {}).get('commits', '?')} commits yours") if x)}
        if k == "skill":
            d["runs"], d["unit"] = used.get(a["name"], 0), "uses"
        elif k == "command":
            d["runs"], d["unit"] = typed.get(a["name"], 0), "times typed"
        elif k == "output_style":
            on_in = active.get(a["name"].lower())
            d["on"] = bool(on_in)
            if on_in:
                d["text"] += " · switched on in " + ", ".join(on_in)
        seen[key] = d
        out.append(d)
    for d in out:
        if d["copies"] > 1:
            d["source"] += f" · {d['copies']} copies"
    mcp_calls = {r["what"]: r["calls"] for r in ((inv.get("tools") or {}).get("typed_chats") or {}).get("by_mcp_server", [])}
    for m in inv.get("mcp_servers") or []:
        out.append({"kind": "mcp", "label": m["name"], "source": f"{m['scope']} · {m.get('transport') or '?'}",
                    "runs": mcp_calls.get(m["name"], 0), "unit": "tool calls", "own": None, "event": "", "on": None,
                    "text": " · ".join(str(x) for x in (short(m.get("source")), m.get("command"), m.get("url_host")) if x)})
    for s in inv.get("settings") or []:
        nb = s.get("notable") or {}
        rules = [f"{len(s.get(k) or [])} {k.replace('_', ' ')}" for k in ("deny_rules", "ask_rules") if s.get(k)]
        if not (nb or rules or s.get("default_mode")) or "/worktrees/" in s["path"] or "/w/" in s["path"]:
            continue
        vals = [f"{k} = {v}" for k, v in nb.items() if v not in ({}, [], None)]
        out.append({"kind": "setting", "label": short(s["path"]), "source": s["scope"], "runs": None, "unit": "",
                    "own": None, "event": "", "on": None,
                    "text": "; ".join(vals + rules + ([f"default mode {s['default_mode']}"] if s.get("default_mode") and "permissions.defaultMode" not in nb else []))})
    have = {d["label"] for d in arts if d["kind"] == "eval"}
    for e in inv.get("evals") or []:
        p = short(e["path"]).rstrip("/").split("/")
        label = "/".join(p[-3:-1]) if len(p) >= 3 else p[-1]
        if label in have:
            continue
        out.append({"kind": "eval", "label": label, "source": "no runs or cases logged", "runs": 0, "unit": "runs",
                    "own": (e["theirs"] * 2 >= e["commits"] > 0) if e.get("commits") else None, "event": "", "on": None,
                    "text": f"{short(e['path'])} · {e.get('files', '?')} files · {e.get('case_files', 0)} cases · last run {e.get('last_run')}"})
    return out


def _part(esc, d, extra=""):
    body = esc((d["text"] or "").strip()[:700])
    sub = d["source"]
    if d["runs"] is not None:
        sub = (sub + " · " if sub else "") + f"{_n(d['runs'])} {d['unit']}"
    return (f'<details class="part"><summary>{esc(d["label"])}{extra}'
            f'{"<small>" + esc(sub) + "</small>" if sub else ""}</summary><pre>{body}</pre></details>')


def _stack(esc, parts, top=4, key=lambda d: -(d["runs"] or 0)):
    """The top few parts, the rest behind one 'Show all N'."""
    if not parts:
        return '<p class="none">Nothing found in this run.</p>'
    ps = sorted(parts, key=key)
    out = "".join(_part(esc, d) for d in ps[:top])
    if len(ps) > top:
        out += (f'<details class="more"><summary>Show all {len(ps)}</summary>'
                + "".join(_part(esc, d) for d in ps[top:]) + "</details>")
    return out


def render(ctx):
    esc = ctx["esc"]
    inv, ana, art = ctx.get("inventory"), ctx.get("anatomy"), ctx.get("cheap_artifacts")
    prob, res = ctx.get("cheap_problems"), ctx.get("v15_result")
    if not inv:
        return '<p class="empty">Not in this run: inventory.json is missing.</p>'
    out = []
    days = inv["meta"].get("days", 30)

    # ---- 1. portrait ------------------------------------------------------------------------------------------
    typed, prog = inv["chats"]["typed_chats"], inv["headless"]["runs"]
    real = [r for r in inv["repos"] if r["repo"].startswith("/")]
    cs = commit_kit.commit_share(inv)
    mine, allc = cs["mine"], cs["all"]
    # never a share of the two: a headless run and a typed session are different units (his 11-04 review)
    head = f"You typed in {_n(typed)} sessions, and your scripts started {_n(prog)} headless runs."
    hc = inv.get("hook_context") or {}
    lines = [f"{_n(mine)} of the {_n(allc)} commits in the {cs['repos']} repos you write in, in the last {days} days, are yours.",
             f"Sub-agents ran {_n(inv['helpers']['calls'])} times across {_n(inv['helpers']['chats'])} sessions."]
    if hc.get("typed_chats_with_hook_text"):
        lines.append(f"Something you set up (a hook or a rule file) loaded text into Claude's context in {_n(hc['typed_chats_with_hook_text'])} "
                     f"of {_n(hc['typed_chats'])} typed sessions.")

    names = {}
    for D in (res or {}).get("disciplines", []):
        for p in D["practices"] + D["challenges"]:
            names[p["id"]] = p["name"]
    strengths = [e for e in (res or {}).get("evidence", []) if e.get("kind") == "practice" and e.get("rung") == "L4"]
    weak = [r for r in (prob or {}).get("rows", []) if r.get("shows") == "shows"]
    det = (res or {}).get("detectors", {})
    def _items(rows, kind, icon, label=None):
        pk = "c" if kind == "weak" else "p"
        lis = [f'<li><details><summary>{icon}<span>{PK.link_html(pk, i, label(i) if label else esc(_short(i, names)))}</span></summary>'
               f'<div class="why">{w}</div></details></li>' for i, w in rows]
        if not lis:
            return '<p class="none">None in this run.</p>'
        head, rest = lis[:6], lis[6:]
        more = (f'<li><details class="more"><summary>{len(rest)} more</summary><ul class="list {kind}">{"".join(rest)}</ul>'
                f'</details></li>') if rest else ""
        return f'<ul class="list {kind}">{"".join(head)}{more}</ul>'
    good = _items([(e["id"], f'<p>{esc(e.get("why"))}</p>') for e in strengths], "good", ICON_OK)
    bad = _items([(r["id"], f'<p>{esc(r.get("why"))}</p>{check_kit.block(r, limit=3)}') for r in weak], "weak", ICON_WARN,
                 label=lambda i: f'<b>{esc(_term(i))}</b><span class="def">{esc(names.get(i, ""))}</span>')
    split = (f'<div class="split"><div><h3>{"Practices that run without you" if ctx.get("member") else "Practices at L4"} <span class="c">{len(strengths)}</span></h3>{good}</div>'
             f'<div><h3>Problems that show up in your logs <span class="c">{len(weak)}</span></h3>{bad}</div></div>')
    top = sorted(real, key=lambda r: -(r["typed_chats"] + r["headless_runs"] + r["theirs_in_window"]))[:4]
    projects = ('<table class="proj"><thead><tr><th>Most active projects</th><th>Sessions you typed</th>'
                '<th>Headless runs</th><th>Your commits</th></tr></thead><tbody>'
                + "".join(f'<tr><td>{esc(r["repo"].rstrip("/").rsplit("/", 1)[-1])}</td><td>{_n(r["typed_chats"])}</td>'
                          f'<td>{_n(r["headless_runs"])}</td><td>{_n(r["theirs_in_window"])}</td></tr>' for r in top)
                + "</tbody></table>")
    how = _how(esc, [
        ("Headline", [f"inventory.json chats.typed_chats ({_n(typed)}) and headless.runs ({_n(prog)}): "
                      "the two are shown side by side, never as a share: a run and a session are different units. A headless run is a log whose entry "
                      "point is the SDK or `claude -p`, not a terminal session.",
                      f"{_n(inv['headless'].get('while_away', inv['headless'].get('started_with_no_typed_message_in_30_min_before', 0)))} of those "
                      "began while you were away (no typed message in the 30 minutes before, or a scheduled job started them); "
                      "the rest may follow a command you typed."]),
        ("Carries", f"v15-result.json evidence[] where kind = practice and the practice runs without you ({len(strengths)}): something you "
                    "built carries it and checks it. Open an item for the sentence it rests on." if ctx.get("member") else
                    f"v15-result.json evidence[] where kind = practice and rung = L4 ({len(strengths)}): something you "
                    "built carries it and checks it. Open an item for the sentence it rests on."),
        ("Slips", f"cheap-problems.json rows[] where shows = \"shows\" ({len(weak)}): the problem's own measure found it "
                  "in your logs. \"Doesn't show\" and \"can't tell\" are left out."),
        ("Projects", f"inventory.json repos[]: the four with the most typed sessions + runs + your commits. A commit is yours "
                     f"when git shows it under {', '.join(inv['person']['identities'])}. Last {days} days."),
        ("Decided by", CODE), ("Checked", UNCHECKED)])
    out.append(f'<p class="lede">{esc(head)}</p><details class="morefacts"><summary>The numbers behind it</summary><p class="facts">{esc(" ".join(lines))}</p></details>{split}{projects}{how}')

    # ---- 2. how one task moves --------------------------------------------------------------------------------
    arts = [_parse(a, {x["name"]: x["origin"] for x in (ana or {}).get("artifacts", []) if x["kind"] == "agent"})
            for a in (art or [])]
    arts += _setup_items(inv, arts)
    by = lambda k: [d for d in arts if d["kind"] == k]  # noqa: E731
    hooks = by("hook")
    ev = lambda *names: [d for d in hooks if any(d["event"].startswith(n) for n in names)]  # noqa: E731
    stores = sorted((ana or {}).get("memory_stores", []), key=lambda s: -s.get("files", 0))
    store_parts = [{"label": s["path"].replace((ana["meta"].get("home") or ""), "~", 1)[-44:], "source": s["kind"].replace("_", " "),
                    "runs": s.get("files"), "unit": "files", "own": None, "kind": "store",
                    "text": f"{s['path']}\n{s.get('files', 0)} files, {s.get('md_files', 0)} notes, "
                            f"newest {str(s.get('newest_mtime', ''))[:10]}"} for s in stores]
    plan = inv.get("plan_dialogs") or {}
    rot = ((inv.get("probes") or {}).get("context-rot") or {}).get("symptom", {})
    step = rot.get("step_in_messages_2_10")
    hand = [{"label": f"{_n(typed)} sessions you typed", "source": f"{_n(inv['chats']['typed_messages'])} messages", "runs": None,
             "unit": "", "text": "inventory.json chats.typed_chats and chats.typed_messages: what you handed over by typing."},
            {"label": f"{_n(prog)} headless runs", "source": "started by a script, nobody typing", "runs": None, "unit": "",
             "text": "inventory.json headless.runs: runs started by a script, a schedule or another tool."}]
    stepin = ([{"label": f"You corrected or stopped it on {round(step * 100, 1)}% of messages 2\u201310", "source": "per session, last 30 days",
                "runs": None, "unit": "", "text": "inventory.json probes.context-rot.symptom.step_in_messages_2_10 = "
                f"{step}: a correction, a stop or a rejected tool call as a share of messages 2 to 10 of a session."}] if step is not None else []) \
        + ev("PreToolUse") + ([{"label": f"{plan['calls']} plan dialogs, {plan['refused']} rejected",
                                   "source": "you say yes or no", "runs": None, "unit": "",
                                   "text": "inventory.json plan_dialogs: the agent asked you to approve a plan "
                                           f"{plan['calls']} times in {plan['chats']} sessions; you rejected {plan['refused']}."}] if plan else [])
    stepin_note = (f"You corrected or stopped the agent on {round(step * 100, 1)}% of messages 2–10 of a session."
                   if step is not None else "")
    stages = [
        ("1. The task starts", True, "You type it, or a headless run starts it", hand, "You act here"),
        ("2. Context loads", False, "SessionStart and UserPromptSubmit hooks", ev("SessionStart", "UserPromptSubmit"), ""),
        ("3. Claude works", False, "Headless runs, your agents, PostToolUse hooks",
         by("runner") + by("launch") + by("agent") + ev("PostToolUse"), ""),
        ("4. You approve or interrupt", True, "PreToolUse hooks, plans you approve", stepin, "You act here"),
        ("5. The work is checked", False, "Stop hooks, tests, evals", ev("Stop") + by("test") + by("eval"), ""),
        ("6. Memory is saved", False, "Memory stores", store_parts, ""),
    ]
    cols = ""
    for i, (name, person, who, parts, tag) in enumerate(stages, 1):
        sk = (lambda d: 0) if i in (1, 4) else (lambda d: -(d["runs"] or 0)) if i != 3 else (lambda d: (d["kind"] != "runner" and d["kind"] != "launch", -(d["runs"] or 0)))
        cols += (f'<li class="stage{" you" if person else ""}"><div class="node"><i></i></div><h5>{esc(name)}</h5>'
                 + (f'<p class="youtag">{esc(tag)}</p>' if tag else "")
                 + f'<p class="who">{esc(who)}</p>' + _stack(esc, parts, 3, sk) + "</li>")
    flow_how = _how(esc, [
        ("Where", ["cheap-artifacts.json: hooks by the event named first in their text; runner, launch and agent "
                   "items by kind; test and eval items by kind.",
                   "anatomy.json memory_stores[] for what is kept; inventory.json chats, headless and plan_dialogs for the "
                   "hand-over and step-in."]),
        ("Rule", ["Step by event: SessionStart and UserPromptSubmit = context loads; PreToolUse = you approve or interrupt "
                  "(you or a rule can stop it); PostToolUse, runners, headless launch scripts and agents = Claude works; Stop hooks, tests and "
                  "evals = the work is checked; memory stores = memory is saved.",
                  "Parts in a stage are ordered by how often they ran in the window (runs, sub-agent calls, files). "
                  "Only 3 are shown per stage; the rest is behind Show all.",
                  stepin_note or "Step-in rate: not in this run."]),
        ("Decided by", "Code, by the event or kind the item was found as. A hook belongs to one stage even when it "
                       "does more than one job. " + UNCHECKED),
        ("Can't tell", "Which stage a program really plays in the person's own head; whether the checks catch anything "
                       "(that is the Practice view's question). Click a part to read the text it was found with.")])
    out.append('<h4>Where your setup acts on a task, step by step</h4><p class="sub">Six steps, left to right. A filled dot is a step where you act yourself.</p>'
               f'<ol class="flow">{cols}</ol>{flow_how}')

    # what they built is the board on its own tab, one source with the share file (board.py), not a second count here:
    # a tester 11-06, "'What you built' then repeats in the section of same name. Duplicated numbers are especially irritating"
    out.append('<p class="sub">What you built, part by part: <a href="#built">What you\'ve built</a>.</p>')
    return "".join(out)
