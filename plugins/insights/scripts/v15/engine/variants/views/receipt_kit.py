"""Small helpers shared by the two "precisely" views (view_practices, view_problems). Not a view: frame.py only loads view_*.py.

Plain words for a source id, tolerant reading of the raw-number strings the detectors store, and the card CSS.
Stdlib only.
"""
import ast
import html
import os
import re

esc = lambda s: html.escape("" if s is None else str(s))  # noqa: E731

_WORDS = [(r"\bThey\b", "You"), (r"\bthey\b", "you"), (r"\bTheir\b", "Your"), (r"\btheir\b", "your"),
          (r"\btheirs\b", "yours"), (r"\bthemselves\b", "yourself"), (r"\bthem\b", "you"),
          (r"\bhis\b", "your"), (r"\bhim\b", "you"), (r"\bhe\b", "you")]


def receipt(s):
    """An evidence line or our rule, as the detector wrote it. Only 'theirs a/b' (authorship) becomes 'yours'; a
    mid-line 'they/them' is usually a thing (runs, checks), so it stays. The rung arrow reads as a word."""
    s = re.sub(r"\btheirs\b", "yours", "" if s is None else str(s))
    return re.sub(r"\s*->\s*(L[1-4])( text)?\s*$", r" → \1", s)


def plain(s):
    """Second person, nothing else touched: an evidence line is a receipt, so its words stay as the detector wrote them."""
    s = "" if s is None else str(s)
    for pat, to in _WORDS:
        s = re.sub(pat, to, s)
    return s


def pretty_key(k):
    return plain(str(k).replace("_", " "))


def _base(path):
    return os.path.basename(path.rstrip("/")) or path


def _tail(path, n=2):
    return "/".join(path.rstrip("/").split("/")[-n:])


_FIXED = {
    "chats.jsonl": "Your session logs", "chats.jsonl calls": "Your session logs",
    "chats_logged + tools.typed_chats.by_mcp_server + settings": "Your session logs and settings",
    "substrate laptop_signs": "What the playbook says to look for",
    "What they do by hand": "What you do by hand", "Their own skills": "Your own skills",
    "Instruction files": "Instruction files", "instruction files": "Instruction files",
    "instruction_files": "Instruction files", "refusals by hooks": "Refusals by your hooks",
    "hook_scripts": "Hook scripts", "git hooks": "Git hooks", "memory stores": "Memory stores",
    "past_work_reach": "Measured from your logs · past work reaching the agent",
    "comprehension-debt.by_repo": "Measured from your git history · per repo",
    "settings that limit the agent": "Settings that limit the agent",
    "Programs that start agents": "Programs that start agents", "Headless runs": "Headless runs",
    "headless runs": "Headless runs", "schedule logs": "Schedule logs", "workflows": "Workflows",
}


def source_label(src):
    """`hook:notify:notify.sh` -> "Hook · notify.sh"; `probes.x` -> "Measured from your logs · x"."""
    src = (src or "").strip()
    if src in _FIXED:
        return _FIXED[src]
    if " / " in src:
        return " + ".join(source_label(p) for p in src.split(" / "))
    m = re.match(r"probes\.([\w-]+)((?:\.[\w]+)*)$", src)
    if m:
        extra = " (" + pretty_key(m.group(2).strip(".").replace(".", " ")) + ")" if m.group(2) else ""
        return "Measured from your logs · " + m.group(1).replace("_", " ").replace("-", " ") + extra
    m = re.match(r"(hook|runner|agent|plugin|eval|test|tool):?(.*)$", src)
    if m:
        kind, rest = m.groups()
        word = {"hook": "Hook", "runner": "Program", "agent": "Agent", "plugin": "Plugin", "eval": "Eval",
                "test": "Test", "tool": "Tool"}[kind]
        if kind == "hook":
            rest = rest.split(":")[-1]
        elif kind in ("runner", "eval"):
            rest = _base(rest) if kind == "runner" else _tail(rest)
        return f"{word} · {rest}" if rest else word
    return src[:1].upper() + src[1:].replace("_", " ")


# --- the raw-number strings -------------------------------------------------------------------------------------

_NUM = r"-?\d+(?:\.\d+)?"
_VAL = rf"({_NUM}|'[^']*'|\"[^\"]*\"|true|false|null|None|True|False)"
_PAIR = re.compile(rf"""['"]([^'"]+)['"]\s*:\s*{_VAL}""")
_NEST = re.compile(r"""['"]([^'"]+)['"]\s*:\s*\{([^{}]*)(?:\}|$)""")


def _val(v):
    return v.strip("'\"") if v[:1] in "'\"" else v


def parse_raw(detail):
    """-> ("table", cols, rows) | ("pairs", [(k, v)], cut) | ("list", [(tag, text)]) | ("text", s).
    The data store cut some strings at 200 characters, so a broken dict is read as far as it goes (cut = True)."""
    d = (detail or "").strip()
    if d[:1] in "{[":
        try:
            v = ast.literal_eval(d)
        except (ValueError, SyntaxError):
            v = None
        if isinstance(v, dict):
            if v and all(isinstance(x, dict) for x in v.values()):
                cols = list(dict.fromkeys(k for x in v.values() for k in x))
                return ("table", cols, [(k, [x.get(c, "") for c in cols]) for k, x in v.items()], False)
            return ("pairs", [(k, x) for k, x in v.items()], False)
        if isinstance(v, list):
            items = [(i[0], i[1]) if isinstance(i, (tuple, list)) and len(i) == 2 else ("", i) for i in v]
            return ("list", items)
        if d[:1] == "{":
            nests = _NEST.findall(d[1:])
            if nests:
                inner = [(k, dict((a, _val(b)) for a, b in _PAIR.findall(body))) for k, body in nests]
                cols = list(dict.fromkeys(c for _, x in inner for c in x))
                return ("table", cols, [(k, [x.get(c, "") for c in cols]) for k, x in inner], True)
            pairs = [(a, _val(b)) for a, b in _PAIR.findall(d)]
            if pairs:
                return ("pairs", pairs, True)
    return ("text", d)


def render_raw(detail):
    kind, *rest = parse_raw(detail)
    if kind == "text":
        return f'<div class="mono">{esc(plain(rest[0]))}</div>'
    if kind == "list":
        li = "".join(f'<li>{f"<span class=tag>{esc(t)}</span> " if t else ""}{esc(x)}</li>' for t, x in rest[0])
        return f'<ul class="mono items">{li}</ul>'
    if kind == "pairs":
        pairs, cut = rest
        rows = "".join(f"<tr><th>{esc(pretty_key(k))}</th><td>{esc(v)}</td></tr>" for k, v in pairs)
        return f'<table class="kv"><tbody>{rows}</tbody></table>' + (
            '<p class="note">The stored figures stop here: the rest was cut off.</p>' if cut else "")
    cols, rows, cut = rest
    head = "".join(f"<th>{esc(pretty_key(c))}</th>" for c in cols)
    body = "".join(f"<tr><th>{esc(k)}</th>" + "".join(f"<td>{esc(v)}</td>" for v in vals) + "</tr>" for k, vals in rows)
    return (f'<div class="scroll"><table class="kv grid"><thead><tr><th></th>{head}</tr></thead><tbody>{body}</tbody>'
            '</table></div>' + ('<p class="note">The stored figures stop here: the rest was cut off.</p>' if cut else ""))


def css(name):
    """Card CSS shared by both views. Sizes 12/14/18 only; colours only from the frame's palette."""
    p = f".v-{name}"
    return f"""
{p} [hidden]{{display:none!important}}
{p} .bar{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 16px}}
{p} .chip{{all:unset;cursor:pointer;box-sizing:border-box;padding:4px 12px;border:1px solid #EAECF0;border-radius:999px;
  background:#FFFFFF;color:#475467;font:500 12px/1.5 Inter,system-ui,sans-serif}}
{p} .chip:hover{{border-color:#B1B8FF}}
{p} .chip:focus-visible,{p} summary:focus-visible,{p} .openall:focus-visible{{outline:2px solid #5258FB;outline-offset:2px}}
{p} .chip[aria-pressed=true]{{border-color:#B1B8FF;background:#F5F5FF;color:#272594}}
{p} .chip b{{font-weight:600;margin-left:4px}}
{p} .openall{{all:unset;cursor:pointer;margin-left:auto;font:500 12px/1.5 Inter,system-ui,sans-serif;color:#3C3ED4;padding:4px 8px}}
{p} .area{{margin:0 0 32px}}
{p} .area>h3{{font-size:18px;font-weight:600;margin:0 0 4px}}
{p} .area .sub{{color:#475467;margin:0 0 4px}}
{p} .area .counts{{font-size:12px;color:#667085;margin:0 0 12px}}
{p} .card{{background:#FFFFFF;border:1px solid #EAECF0;border-radius:12px;margin:0 0 8px}}
{p} .card[open]{{border-color:#B1B8FF}}
{p} .card>summary{{list-style:none;cursor:pointer;padding:12px 16px 12px 40px;position:relative;display:flex;flex-wrap:wrap;
  gap:2px 12px;align-items:center}}
{p} .card>summary::-webkit-details-marker{{display:none}}
{p} .card>summary::before{{content:"";position:absolute;left:16px;top:18px;width:6px;height:6px;border-right:2px solid #667085;
  border-bottom:2px solid #667085;transform:rotate(-45deg);transition:transform .12s}}
{p} .card[open]>summary::before{{transform:rotate(45deg);top:16px}}
{p} .nm{{font-weight:600}}
{p} .one{{flex:1 1 100%;color:#475467;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}}
{p} .card[open] .one{{display:none}}
{p} .pill{{font-size:12px;font-weight:500;padding:0 8px;border-radius:999px;border:1px solid #D5D5FE;background:#F5F5FF;color:#272594;white-space:nowrap}}
{p} .pill.none{{border-color:#EAECF0;background:#F9FAFB;color:#475467}}
{p} .pill.shows{{border-color:#FDF1D9;background:#FEFBF5;color:#B54708}}
{p} .pill.ok{{border-color:#EAECF0;background:#F9FAFB;color:#039855}}
{p} .pill.unk{{border-color:#EAECF0;background:#F9FAFB;color:#667085}}
{p} .could{{font-size:12px;color:#667085}}
{p} .area-tag{{font-size:12px;color:#667085}}
{p} .body{{padding:4px 16px 16px 40px;display:grid;grid-template-columns:1fr;gap:0 32px}}
@media(min-width:900px){{{p} .body.two{{grid-template-columns:1fr 1fr}}}}
{p} .sec{{border-top:1px dotted #D0D5DD;padding:12px 0 0;margin:12px 0 0;min-width:0}}
{p} .sec:first-child{{border-top:0;margin-top:0;padding-top:0}}
@media(max-width:899px){{{p} .body.two>div+div>.sec:first-child{{border-top:1px dotted #D0D5DD;padding-top:12px;margin-top:12px}}}}
{p} .lab{{font-size:12px;font-weight:600;color:#475467;margin:0 0 4px}}
{p} .sec p{{margin:0 0 4px}}
{p} .sec p.dim,{p} .note{{color:#667085;font-size:12px;margin:4px 0 0}}
{p} .ev{{display:grid;grid-template-columns:minmax(0,170px) minmax(0,1fr);gap:4px 12px;margin:0;padding:0;list-style:none}}
{p} .ev li{{display:contents}}
{p} .ev .src{{font-size:12px;font-weight:500;color:#475467}}
@media(max-width:600px){{{p} .ev{{grid-template-columns:1fr}}{p} .ev .src{{margin-top:8px}}}}
{p} .mono{{font:400 12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere;color:#15172F}}
{p} ul.items{{margin:0;padding:0;list-style:none}}{p} ul.items li{{margin:0 0 4px}}
{p} .tag{{color:#667085}}
{p} .scroll{{overflow-x:auto}}
{p} table.kv{{border-collapse:collapse;font:400 12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}}
{p} table.kv th,{p} table.kv td{{text-align:left;padding:2px 16px 2px 0;font-weight:400;vertical-align:top;border-bottom:1px solid #EAECF0}}
{p} table.kv th{{color:#475467}}{p} table.kv td{{color:#15172F;font-variant-numeric:tabular-nums}}
{p} table.kv.grid thead th{{font-weight:500}}
{p} .fold{{margin:4px 0 0}}{p} .fold>summary{{cursor:pointer;font-size:12px;color:#3C3ED4;list-style:none}}
{p} .fold>summary::-webkit-details-marker{{display:none}}
{p} .fold>summary::before{{content:"+ "}}{p} .fold[open]>summary::before{{content:"- "}}
{p} .fold .inner{{margin-top:4px;color:#475467;font-size:12px;overflow-wrap:anywhere}}
{p} .warn{{color:#B54708;font-weight:500}}
{p} .ask{{color:#15172F}}
{p} .missing{{color:#667085}}
"""


def fold(summary, inner_html, cls=""):
    return f'<details class="fold {cls}"><summary>{esc(summary)}</summary><div class="inner">{inner_html}</div></details>'
