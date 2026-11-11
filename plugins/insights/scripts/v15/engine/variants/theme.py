"""Dark mode for any variant page, added after the page is built: `html = theme.apply(html)`.

What it does
- Every colour in the page's CSS, inline styles and SVG colour attributes becomes a CSS variable. Light values are the
  page's own; dark values come from DARK below. The Primary ramp is mirrored (a light tint becomes a deep tint, a deep
  ink becomes a light ink), so "strongest" stays the most visible in both modes.
- A switch (Auto · Light · Dark) in the bottom-right corner. Auto = the system setting, or dark when Dark Reader is on.

Dark Reader (browser extension), read from its source (src/inject/dynamic-theme/index.ts, detector.ts):
- It marks a page it styles with `<meta name="darkreader">` and `html[data-darkreader-mode]`.
- It watches the page head; the moment `<meta name="darkreader-lock">` appears it removes its styling, live
  (`addMetaListener`). That is its documented opt-out for sites with their own dark theme.
- So: when the page sees Dark Reader arrive, it adds the lock, remembers "this person uses Dark Reader" (localStorage),
  and turns its own dark theme on. From then on the page alone decides light or dark, and the switch means what it says
  (Light really is light). On later visits the lock is added before Dark Reader starts, so it never paints at all.
- Not relying on Dark Reader's own "detect dark theme" check: it runs once, can be switched off in its settings, and
  reads a strict `color-scheme: light` meta as "leave it to me", which is what made the 11-03 screenshot half-dark.
"""
import re

# light hex -> dark hex. Grays: surfaces go to near-black neutrals (not Midnight navy: his 11-04 "all blue page"), inks go light. Primary: the ramp mirrored. Status: brand 400/500
# inks, brand 900 washes. Two border/inset values are blends between brand grays (the brand has no dark product palette).
DARK = {
    "FFFFFF": "1B1D23", "FCFCFD": "1B1D23", "F9FAFB": "111216", "F4F5F7": "22242B", "F2F4F7": "22242B",
    "EAECF0": "2E3039", "D0D5DD": "454955", "949BAD": "667085", "667085": "949BAD", "475467": "D0D5DD",
    "344054": "EAECF0", "1D2939": "F4F5F7", "101828": "FFFFFF", "15172F": "F9FAFB",
    "F5F5FF": "1F2145", "EEEFFE": "23266A", "E5E8F9": "262A5C", "DDDEFE": "272594", "D5D5FE": "2C2F6B",
    "CDCFFE": "3C3ED4", "C7C9FD": "3C3ED4", "B1B8FF": "5258FB", "999AFD": "6369FF", "979BFC": "6369FF",
    "5258FB": "999AFD", "3F45E0": "B1B8FF", "3C3ED4": "B1B8FF", "272594": "D5D5FE", "171559": "F5F5FF",
    "039855": "39D87D", "027A48": "74E4A4", "12B76A": "39D87D", "F6FEF9": "15302A", "C4F3D8": "123D2E",
    "DC6803": "F79009", "1570EF": "53B1FD", "7A5AF8": "A48AFB", "B54708": "F8C869", "F79009": "F8C869", "FEFBF5": "2E2621", "FDF1D9": "4A3420",
}

_HEX = re.compile(r"#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b")
_SVG_ATTR = re.compile(r'\b(fill|stroke|stop-color)="(#[0-9A-Fa-f]{3,6})"')


def _full(h):
    h = h.upper()
    return "".join(c * 2 for c in h) if len(h) == 3 else h


def _vars(text, seen):
    def sub(m):
        h = _full(m.group(1))
        seen.add(h)
        return f"var(--c-{h})"
    return _HEX.sub(sub, text)


HEAD = """<meta name="color-scheme" content="light dark">
<script>/* theme: runs before first paint. See theme.py for why each line is here. */
(function () {
  var de = document.documentElement, KEY = 'proxify-theme', DR = 'proxify-uses-darkreader',
      mq = matchMedia('(prefers-color-scheme: dark)');
  function get(s, k) { try { return s.getItem(k); } catch (e) { return null; } }
  function set(s, k, v) { try { s.setItem(k, v); } catch (e) {} }
  function darkReaderHere() {
    return de.hasAttribute('data-darkreader-mode') || de.hasAttribute('data-darkreader-scheme') ||
      !!document.querySelector('meta[name="darkreader"], style.darkreader');
  }
  function pref() { return get(localStorage, KEY) || 'auto'; }
  function wantDark() {
    var p = pref();
    if (p !== 'auto') return p === 'dark';
    return mq.matches || get(localStorage, DR) === '1';
  }
  function apply() {
    var dark = wantDark();
    de.dataset.theme = dark ? 'dark' : 'light';
    de.dataset.themePref = pref();
    de.classList.toggle('dark', dark);
    de.style.colorScheme = dark ? 'dark' : 'light';
    var m = document.querySelector('meta[name="color-scheme"]');
    if (m) m.content = dark ? 'dark' : 'light';
  }
  function lock() {
    if (!document.head || document.querySelector('meta[name="darkreader-lock"]')) return;
    var m = document.createElement('meta'); m.name = 'darkreader-lock'; document.head.appendChild(m);
  }
  function onDarkReader() {
    if (!darkReaderHere()) return;
    set(localStorage, DR, '1');
    lock();   // Dark Reader removes its own styling as soon as this appears
    apply();
  }
  if (get(localStorage, DR) === '1') lock();
  apply();
  mq.addEventListener('change', apply);
  new MutationObserver(onDarkReader).observe(de, {attributes: true, attributeFilter: ['data-darkreader-mode', 'data-darkreader-scheme']});
  new MutationObserver(onDarkReader).observe(document.head || de, {childList: true});
  window.__setTheme = function (p) { set(localStorage, KEY, p); apply(); };
})();
</script>"""

SWITCH_CSS = """
.theme-switch{position:fixed;right:16px;bottom:16px;z-index:50;display:flex;gap:2px;padding:2px;border-radius:999px;
  background:var(--c-FFFFFF);border:1px solid var(--c-EAECF0);box-shadow:0 4px 8px -2px rgba(16,24,40,.1)}
.theme-switch button{all:unset;display:flex;align-items:center;gap:4px;padding:4px 10px;border-radius:999px;cursor:pointer;
  font:500 12px/1.5 Inter,system-ui,sans-serif;color:var(--c-475467)}
.theme-switch button:hover{color:var(--c-15172F)}
.theme-switch button:focus-visible{outline:2px solid var(--c-5258FB);outline-offset:1px}
.theme-switch svg{width:14px;height:14px}
html[data-theme-pref=auto] .theme-switch [data-p=auto],html[data-theme-pref=light] .theme-switch [data-p=light],
html[data-theme-pref=dark] .theme-switch [data-p=dark]{background:var(--c-F4F5F7);color:var(--c-15172F)}
@media print{.theme-switch{display:none}}
/* a lavender band mirrored by the map turns loud Primary/800; in dark it sits as a quiet deep-indigo band instead */
html[data-theme=dark] .hero:has(.donut){background:#1E2050;border-bottom:1px solid var(--c-EAECF0)}
"""

_ICON = {
    "auto": '<path d="M2 3h12v8H2zM6 14h4M8 11v3"/>',
    "light": '<circle cx="8" cy="8" r="3"/><path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3 3l1 1M12 12l1 1M3 13l1-1M12 4l1-1"/>',
    "dark": '<path d="M13.5 10A6 6 0 0 1 6 2.5a6 6 0 1 0 7.5 7.5z"/>',
}
SWITCH = ('<div class="theme-switch" role="group" aria-label="Colour theme">' + "".join(
    f'<button type="button" data-p="{p}" onclick="__setTheme(\'{p}\')"><svg viewBox="0 0 16 16" fill="none" '
    f'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    f'{_ICON[p]}</svg>{p.capitalize()}</button>' for p in ("auto", "light", "dark")) + "</div>")


def apply(html, switch=True):
    """switch=False: dark mode without the corner switch (a frame page whose inner page carries it)."""
    seen = set()
    html = _SVG_ATTR.sub(lambda m: f'style="{m.group(1)}:{_vars(m.group(2), seen)}"', html)
    html = re.sub(r"(<style[^>]*>)(.*?)(</style>)", lambda m: m.group(1) + _vars(m.group(2), seen) + m.group(3), html, flags=re.S)
    html = re.sub(r'style="([^"]*)"', lambda m: f'style="{_vars(m.group(1), seen)}"', html)
    seen |= {"FFFFFF", "EAECF0", "475467", "15172F", "5258FB", "F4F5F7"}  # the switch's own colours
    missing = sorted(h for h in seen if h not in DARK)
    if missing:
        raise SystemExit(f"theme.py: no dark value for {missing}; add them to DARK")
    tokens = (":root{" + "".join(f"--c-{h}:#{h};" for h in sorted(seen)) + "}"
              "html[data-theme=dark]{" + "".join(f"--c-{h}:#{DARK[h]};" for h in sorted(seen)) + "}")
    html = html.replace("<head>", "<head>\n" + HEAD, 1) if "<head>" in html else HEAD + html
    html = html.replace("</style>", tokens + SWITCH_CSS + "</style>", 1)
    if not switch:
        return html
    return html.replace("</body>", SWITCH + "</body>", 1) if "</body>" in html else html + SWITCH


if __name__ == "__main__":
    # python3 theme.py PAGE.html ...  -> adds dark mode in place (skips a page that already has it)
    import sys
    for path in sys.argv[1:]:
        page = open(path).read()
        if "proxify-theme" not in page:
            open(path, "w").write(apply(page))
            print("dark mode added:", path)
