#!/usr/bin/env python3
"""One page at the end of the run: three tabs over the same scan.

  Your level             render_place.py  (opens first: the answer)
  How you work           render_story.py  (the detail)
  How it was worked out  render_explorer.py (each finding with its method; dev-mode story only)

Each tab is the page its own renderer makes, held in an iframe's srcdoc, so their styles never clash and the
result is still one file. Run: python3 -I render_all.py STORY.json FACTS.json LEVEL.json -o my-setup.html
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import render_explorer  # noqa: E402
import render_place  # noqa: E402
import render_story  # noqa: E402

CSS = """
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#15172F;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
.bar{height:56px;display:flex;align-items:center;gap:6px;padding:0 16px;background:#15172F;border-bottom:1px solid rgba(255,255,255,.12)}
.bar .t{color:#D5D5FE;font-weight:600;font-size:13px;letter-spacing:.08em;text-transform:uppercase;margin-right:14px}
.bar button{font:inherit;font-size:15px;font-weight:500;color:#D5D5FE;background:transparent;border:1px solid transparent;
  border-radius:999px;padding:8px 16px;cursor:pointer}
.bar button:hover{background:rgba(255,255,255,.06)}
.bar button[aria-selected=true]{background:#5258FB;color:#fff}
.bar button:focus-visible{outline:2px solid #73E2A3;outline-offset:2px}
iframe{display:none;width:100%;height:calc(100% - 56px);border:0;background:#fff}
iframe.on{display:block}
"""

JS = """
const tabs=[...document.querySelectorAll('.bar button')], frames=[...document.querySelectorAll('iframe')];
function show(i){tabs.forEach((t,j)=>t.setAttribute('aria-selected',j===i));frames.forEach((f,j)=>f.classList.toggle('on',j===i));
  history.replaceState(null,'','#'+tabs[i].dataset.id)}
tabs.forEach((t,i)=>t.addEventListener('click',()=>show(i)));
const start=tabs.findIndex(t=>'#'+t.dataset.id===location.hash);show(start<0?0:start);
"""


def page(tabs: list[tuple[str, str, str]]) -> str:
    buttons = "".join(f'<button role="tab" data-id="{i}" aria-selected="false">{html.escape(label)}</button>'
                      for i, label, _ in tabs)
    frames = "".join(f'<iframe title="{html.escape(label)}" srcdoc="{html.escape(doc, quote=True)}"></iframe>'
                     for _, label, doc in tabs)
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>My agentic setup</title>"
            f"<style>{CSS}</style></head><body><nav class='bar' role='tablist'><span class='t'>My agentic setup</span>"
            f"{buttons}</nav>{frames}<script>{JS}</script></body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("story")
    ap.add_argument("facts")
    ap.add_argument("level")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    story = json.loads(Path(a.story).read_text(encoding="utf-8"))
    facts = json.loads(Path(a.facts).read_text(encoding="utf-8"))
    level = json.loads(Path(a.level).read_text(encoding="utf-8"))
    tabs = [("level", "Your level", render_place.render(facts, level)),
            ("work", "How you work", render_story.render(story))]
    if story.get("dev"):  # the explorer needs the dev-mode story (how each finding was inferred)
        tabs.append(("how", "How it was worked out", render_explorer.render(story)))
    Path(a.out).write_text(page(tabs), encoding="utf-8")
    print(a.out)


if __name__ == "__main__":
    main()
