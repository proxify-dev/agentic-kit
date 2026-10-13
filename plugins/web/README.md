# web

**WebFetchRaw: a web page's own content in a file, where WebFetch gives a small model's answer about it.**

Claude Code's `WebFetch` converts the page to markdown, hands it to a small model with your prompt and returns
that model's reply. Asking it for the page verbatim does not get the page. On 2026-10-13, asked for example.com's
text "verbatim, character for character", it answered:

```
I can't reproduce the full page verbatim because quotes are limited to 125 characters, but here is the closest I can provide:
"Example Domain. This domain is for use in documentation examples without needing permission."
The page also says the domain isn't a service and shouldn't be used for testing or monitoring.
```

The page says "This is not a service; avoid relying on it for testing and monitoring purposes." `WebFetchRaw` writes
the page itself to a file and returns one line:

```
ok: wrote 34038 bytes to /tmp/…/webfetch-raw/docs.python.org/20261012-221501-3_library_json.html-1a2b3c4d.md (markdown, text/html)
```

The page enters the context only when the agent Reads that file, whole or in parts.

```
   the model calls mcp__web__WebFetchRaw { url, path?, output_format?, … }
      └─ hooks/register.ts answers the call itself (a Claude Mod: no MCP server)
           └─ $.process.run: uv runs scripts/webfetch_raw.py, the input as JSON on stdin
                ├─ an HTML page → trafilatura → markdown · text · cleaned html (main content, metadata on top)
                ├─ anything else (JSON, text, markdown, a PDF), or output_format "raw" → the body byte for byte
                └─ stdout: the one ok: line → the tool's result · stderr: error: … → an error result
```

- **In the tool list from the start** (`isDeferred: false`), beside WebFetch, so the model reaches for it when
  it needs exact words, code, numbers or links. Asked for "the exact wording of the paragraph on example.com",
  a `claude -p` session picked it over WebFetch without being told.
- **Not in this module:** the fetch and the extraction. A hooks module has no npm and no Node for an HTML
  extractor, and `$.http.fetch` returns text only (a PDF would not survive), so a Python script does both, one
  process per call; nothing runs between calls.
- **Where the file goes:** `path` (absolute; folders are made), else
  `<tmp>/webfetch-raw/<host>/<time>-<slug>-<hash>.<ext>`.

It replaces an earlier MCP server, `webfetch-verbatim` (tool `WebFetchVerbatim`). Changed on the way: no MCP
server; `raw` is the body as sent (it was prefixed with a header line, and decoded as text); content that is not
HTML is saved as sent, with its own extension; the byte count is bytes.

## Needs

`uv`, and `CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1` in the `env` of your Claude Code settings (`ak setup` switches
it on with this plugin; installed with `/plugin install web@agentic-kit`, set it yourself). The first call builds the plugin's environment (`trafilatura`, `lxml`): a few seconds,
once. A call goes through Claude Code's permission check like any tool's; `mcp__web__WebFetchRaw` in
`permissions.allow` lets it run without asking.

## Check it

```
uv run pytest -q                                  # in plugins/web: the script, against a local HTTP server
claude plugin test plugins/web                    # the mod: registration, the call, its errors
claude plugin validate plugins/web
```
