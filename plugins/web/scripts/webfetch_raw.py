"""WebFetchRaw's work: fetch a URL and save the page's own content to a file, with no model in between.

hooks/register.ts declares the WebFetchRaw tool and runs this file once per call, the call's input as
JSON on stdin. Claude Code's WebFetch hands the page to a small model and returns that model's answer
about it; this writes the page itself to disk: an HTML page extracted to markdown (or text, or cleaned
HTML) by trafilatura, anything else byte for byte. stdout is one status line, which is the tool's result,
so the page enters the context only when the agent Reads the file.

    echo '{"url": "https://example.com"}' | uv run --project plugins/web python plugins/web/scripts/webfetch_raw.py
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import mimetypes
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Literal, Optional

import trafilatura

USER_AGENT = "Mozilla/5.0 (compatible; WebFetchRaw/0.1)"
DEFAULT_DIR = Path(tempfile.gettempdir()) / "webfetch-raw"
EXTRACT_FORMAT = {"markdown": "markdown", "text": "txt", "html": "html"}
EXTENSION = {"markdown": ".md", "text": ".txt", "html": ".html"}


def fetch(url: str, timeout: int) -> tuple[bytes, str, str]:
    """The body as the server sent it, its Content-Type, and the URL it came from after redirects."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.headers.get("Content-Type", ""), resp.geturl()


def is_html(body: bytes, content_type: str) -> bool:
    if content_type:
        return "html" in content_type.lower()
    return body[:512].lstrip().lower().startswith((b"<!doctype html", b"<html"))


def body_extension(url: str, content_type: str) -> str:
    """For a body written as is: the URL's own suffix (README.md stays .md), else the Content-Type's."""
    suffix = Path(urllib.parse.urlparse(url).path).suffix
    if 1 < len(suffix) <= 6:
        return suffix
    return mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".txt"


def default_path(url: str, ext: str) -> Path:
    """<tmp>/webfetch-raw/<host>/<time>-<slug>-<hash><ext>: a new file per fetch."""
    parsed = urllib.parse.urlparse(url)
    host = (parsed.netloc or "unknown").replace(":", "_")
    raw_slug = parsed.path.strip("/").replace("/", "_")
    slug = "".join(c if c.isalnum() or c in "._-" else "_" for c in raw_slug)[:60] or "index"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return DEFAULT_DIR / host / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}-{digest}{ext}"


def web_fetch_raw(
    url: str,
    path: Optional[str] = None,
    output_format: Literal["markdown", "text", "html", "raw"] = "markdown",
    include_links: bool = True,
    include_images: bool = False,
    include_tables: bool = True,
    include_comments: bool = False,
    timeout: int = 30,
    overwrite: bool = True,
) -> str:
    """Fetch url, write it to path (or under DEFAULT_DIR) and return the status line.

    Bad input raises ValueError (FileExistsError for a file overwrite=False keeps); a failed fetch or
    write raises RuntimeError. The arguments are the tool's input schema in hooks/register.ts.
    """
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"url must start with http:// or https:// (got {url!r})")
    if output_format not in (*EXTRACT_FORMAT, "raw"):
        raise ValueError(f"output_format is markdown, text, html or raw (got {output_format!r})")
    dest = Path(os.path.expandvars(os.path.expanduser(path))) if path else None
    if dest is not None and not dest.is_absolute():
        raise ValueError(f"path must be absolute (got {path!r})")
    if dest is not None and dest.exists() and not overwrite:
        raise FileExistsError(f"{dest} exists and overwrite is false")

    # stdout is the tool's result: a library that prints must not add to it
    with contextlib.redirect_stdout(sys.stderr):
        try:
            body, content_type, final_url = fetch(url, timeout)
        except OSError as e:  # URLError, HTTPError, a timeout
            raise RuntimeError(f"fetch failed: {e}") from e
        html = is_html(body, content_type)
        extracted = None
        if html and output_format != "raw":
            extracted = trafilatura.extract(
                body,
                url=final_url,
                output_format=EXTRACT_FORMAT[output_format],
                include_links=include_links,
                include_images=include_images,
                include_tables=include_tables,
                include_comments=include_comments,
                favor_recall=True,
                with_metadata=True,
            )

    if extracted:
        data, fmt, ext = extracted.encode("utf-8"), output_format, EXTENSION[output_format]
    else:
        data, ext = body, ".html" if html else body_extension(final_url, content_type)
        fmt = "raw: trafilatura found no main content" if html and output_format != "raw" else "raw"
    dest = dest or default_path(url, ext)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    except OSError as e:
        raise RuntimeError(f"write failed ({dest}): {e}") from e

    redirected = f", from {final_url}" if final_url != url else ""
    return f"ok: wrote {len(data)} bytes to {dest} ({fmt}, {content_type or 'no content type'}{redirected})"


def main() -> int:
    """The tool call's input as JSON on stdin; the status line on stdout, or `error:` on stderr."""
    try:
        args = json.load(sys.stdin)
        if not isinstance(args, dict):
            raise ValueError("stdin must be a JSON object of the tool's input")
        print(web_fetch_raw(**args))
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (ValueError, TypeError) as e:  # bad JSON, a missing url, an unknown argument
        print(f"error: {e}", file=sys.stderr)
        return 64
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
