"""WebFetchRaw against a local HTTP server: what lands in the file, and the one line it returns."""
from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import webfetch_raw

SCRIPT = Path(webfetch_raw.__file__)

ARTICLE = b"""<!doctype html><html><head><title>Exact Words</title></head><body>
<nav><a href="/">Home</a> <a href="/about">About</a></nav>
<article><h1>Exact Words</h1>
<p>The cache keeps 4096 entries and evicts the oldest after 15 minutes, not the least used.</p>
<p>Read the <a href="https://example.org/spec">specification</a> before changing the limit.</p>
</article>
<footer>Copyright 2026</footer></body></html>"""

PAGES = {
    "/article": (ARTICLE, "text/html; charset=utf-8"),
    "/data.json": (b'{"limit": 4096}\n', "application/json"),
    "/README.md": (b"# Title\n\nSome *markdown*.\n", "text/plain; charset=utf-8"),
    "/empty": (b"<html><body></body></html>", "text/html"),
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/moved":
            self.send_response(301)
            self.send_header("Location", "/article")
            self.end_headers()
            return
        if self.path not in PAGES:
            self.send_error(404)
            return
        body, ctype = PAGES[self.path]
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def default_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(webfetch_raw, "DEFAULT_DIR", tmp_path / "default")


def written(line: str) -> Path:
    assert line.startswith("ok: wrote ")
    return Path(line.split(" to ", 1)[1].split(" (", 1)[0])


def test_an_html_page_is_its_main_content_as_markdown_word_for_word(site, tmp_path):
    line = webfetch_raw.web_fetch_raw(f"{site}/article", path=str(tmp_path / "a.md"))
    text = (tmp_path / "a.md").read_text()
    assert "The cache keeps 4096 entries and evicts the oldest after 15 minutes, not the least used." in text
    assert "https://example.org/spec" in text
    assert "Copyright 2026" not in text
    assert line == f"ok: wrote {len(text.encode())} bytes to {tmp_path / 'a.md'} (markdown, text/html; charset=utf-8)"


def test_raw_is_the_body_byte_for_byte(site, tmp_path):
    webfetch_raw.web_fetch_raw(f"{site}/article", path=str(tmp_path / "a.html"), output_format="raw")
    assert (tmp_path / "a.html").read_bytes() == ARTICLE


def test_content_that_is_not_html_is_written_as_sent_with_its_own_extension(site):
    assert written(webfetch_raw.web_fetch_raw(f"{site}/data.json")).read_bytes() == PAGES["/data.json"][0]
    md = written(webfetch_raw.web_fetch_raw(f"{site}/README.md"))
    assert md.suffix == ".md" and md.read_bytes() == PAGES["/README.md"][0]


def test_a_page_with_no_main_content_falls_back_to_the_body_and_says_so(site):
    line = webfetch_raw.web_fetch_raw(f"{site}/empty")
    assert "raw: trafilatura found no main content" in line
    assert written(line).read_bytes() == PAGES["/empty"][0]


def test_without_a_path_the_file_goes_under_the_default_dir_by_host(site, tmp_path):
    out = written(webfetch_raw.web_fetch_raw(f"{site}/article"))
    assert out.parent.parent == tmp_path / "default" and out.suffix == ".md"


def test_a_redirect_is_followed_and_named(site, tmp_path):
    line = webfetch_raw.web_fetch_raw(f"{site}/moved", path=str(tmp_path / "m.md"))
    assert line.endswith(f", from {site}/article)")


@pytest.mark.parametrize("url, path, message", [
    ("ftp://example.org/x", None, "must start with http"),
    ("https://example.org/", "relative/out.md", "path must be absolute"),
])
def test_bad_input_is_refused_before_any_fetch(url, path, message):
    with pytest.raises(ValueError, match=message):
        webfetch_raw.web_fetch_raw(url, path=path)


def test_overwrite_false_keeps_an_existing_file(site, tmp_path):
    (tmp_path / "keep.md").write_text("mine")
    with pytest.raises(FileExistsError):
        webfetch_raw.web_fetch_raw(f"{site}/article", path=str(tmp_path / "keep.md"), overwrite=False)
    assert (tmp_path / "keep.md").read_text() == "mine"


def test_an_http_error_is_a_tool_error(site):
    with pytest.raises(RuntimeError, match="404"):
        webfetch_raw.web_fetch_raw(f"{site}/missing")


# What hooks/register.ts relies on: the tool's input as JSON on stdin, the result on stdout, a failure on stderr.

def cli(payload: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)], input=payload, capture_output=True, text=True)


def test_the_script_takes_the_tool_input_on_stdin_and_prints_the_one_line(site, tmp_path):
    ran = cli(json.dumps({"url": f"{site}/article", "path": str(tmp_path / "c.md")}))
    assert ran.returncode == 0 and ran.stderr == ""
    assert ran.stdout.startswith(f"ok: wrote ") and ran.stdout.count("\n") == 1
    assert "4096 entries" in (tmp_path / "c.md").read_text()


@pytest.mark.parametrize("payload, code, message", [
    ('{"url": "ftp://x"}', 64, "must start with http"),
    ('{"url": "https://example.org/", "colour": "red"}', 64, "colour"),
    ("not json", 64, "error:"),
    ('["https://example.org/"]', 64, "JSON object"),
])
def test_bad_input_exits_64_with_error_on_stderr(payload, code, message):
    ran = cli(payload)
    assert (ran.returncode, ran.stdout) == (code, "")
    assert ran.stderr.startswith("error: ") and message in ran.stderr


def test_a_failed_fetch_exits_1(site):
    ran = cli(json.dumps({"url": f"{site}/missing"}))
    assert ran.returncode == 1 and "404" in ran.stderr
