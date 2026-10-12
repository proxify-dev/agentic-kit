# Request path, identity, trace and policy

What happens to one request, who the gateway thinks sent it, what it writes down, and what rules it
applies. The trace's field contract for readers is in [trace.md](trace.md).

## Request by request

```
   /v1/messages       engine.serve → pick account (route pin > client's /tc-acct/ prefix > rotation)
                      → ensureTokenFresh → inject → rewrite metadata account_uuid → strip orphan
                      tool blocks (not on a thread continue) → forward → learn quota from headers →
                      on 429/5xx/401/403 the loop (accounts.md, "When an account says no") → strip
                      the per-org overage headers from the client's copy (stripOverageHeaders, default on)
   passthrough        /v1/oauth/token, /v1/code/, uploads — the client's own credential, untouched
   telemetry          /api/event_logging* — answered 200 here, never forwarded (older clients: 2.1.281
                      sends it straight to api.anthropic.com, see security.md)
   dry run            any request with x-brain-dry-run — traced, answered with a canned reply here, never
                      forwarded; before policy, routes and accounts (src/dry-run.ts, xray.md)
```

The trace taps the response on the way out (ttfb, bytes, usage), and records which account served
and how it was chosen (`pinned` · `preferred` · `rotated`). `status` is what the client got: `null`
when no reply head was sent — the engine reset the socket (`error: "upstream: …"`) or the client
left first — and a stream cut after its head keeps the sent status with `"upstream stream: …"`.

See [accounts.md](accounts.md#when-an-account-says-no) for the retry and failover table.

## Identity

`x-brain-run / agent / item / project / origin` headers, sent through `ANTHROPIC_CUSTOM_HEADERS`.
`cc-gateway run`, `env` and the cc-shim fragment send `origin: cli` and the working folder's name as
`project`; `try`, `probe` and `xray` send their own origin and a run id; a launcher of your own can set
any of them (the agentic kit's desktop app sends the run, agent and work item it launched). A proxy
knows only a session id; these headers let a trace row say which job a call belonged to, and let a
policy rule match on it. They are in `IDENTITY_HEADERS` and never forwarded upstream.

## Trace

One NDJSON row per request, plus content-addressed blobs. Each row keeps the API's `messageId`, so a
session's calls join to its transcript steps; the fields readers rely on are in [trace.md](trace.md).
`cc-gateway tail --session [ID]` lists one session's raw rows, the caller's by default.

- **Headers are never written.** No code path in `trace.ts` accepts them, so the rule cannot be
  broken by a later edit that "just adds a field". A test asserts no header name or value reaches disk.
- **Bodies are content-addressed.** A turn resends the whole transcript; the system blocks and tool
  schemas are byte-identical across thousands of requests, so they are stored once. That is also what
  makes "when did the system prompt change" a query instead of a mitmproxy ritual.
- **`capture` saves what no transcript holds** (`trace.ts` `putCall`). A Claude Code transcript
  already holds every message, the whole system prompt and every tool of its session — proven byte
  for byte on 2026-09-25; what it does not hold, the gateway saves:

  ```
     none   nothing
     meta   system prompt · tools · settings (max_tokens, thinking, context_management…) · safeguards
            — small, content-addressed, kept for good; the body as a hash
     gaps   meta, plus (default)
            · the whole body when no transcript will hold it: a session with no transcript file
              when the call arrives (`claude -p` saving nothing), count_tokens      row.recorded: false
            · a side call's newest message (title, recap, progress, WebFetch page…)  blobs.tail
     full   meta, plus every body — the whole conversation again on every call: 8.6 GB in four days
            (measured 2026-09-25). To dig into something, not to leave on.
  ```

  Claude Code writes `<config>/projects/<slug>/<session>.jsonl` before its first model call (1,678 of
  1,678 sessions), so the file's presence when the call arrives is what `recorded` means
  (`transcripts.ts`).
- **Bodies and tails are kept `blobDays`** (default 7; 0 = forever); the small parts stay for good —
  for a bare `claude -p` run they are the only copy of its system prompt. A body or tail goes once
  every row naming it is past the window, unless a pin (`<dataDir>/pins/*.ndjson`), a saved
  `xray -o` run, or a request in flight names it — a minute after boot, then every six hours; each
  day's rows are read once, as they expire (`blobs/.swept`). Rows stay: they hold hashes, never prompt
  text. `/_gateway/status` shows `blobDays` and the last pass (`swept`).
- **Routing hints.** Claude Code's routing hints land parsed in `hints` — request class (main ·
  subagent · compaction · auxiliary), subagent type, compaction markers, and per-tool durations since
  the previous request. Parsed fields, not headers; `tail` shows the class.
- **Old rows.** Rows written before the gateway held its own accounts carry `upstream: chain|direct`
  and, for `chain`, `accountSource: observed`; newer rows have neither.

### Usage counting

`usage` is each count at the **latest** value the API reported, never a sum (`UsageReader`,
`src/trace.ts`). A stream's `message_start` carries input and cache counts and a placeholder output;
`message_delta` carries output as a running total and now repeats input and cache as totals too.
Builds before this rule summed the two events, so their streamed rows (`stream: true` with a
`stopReason` — the delta arrived) hold input, `cacheRead` and `cacheCreation` at exactly 2× and
`output` high by `message_start`'s placeholder (typically 1–20 tokens, not recoverable). Non-streamed
rows, and streams cut before the delta, were right. Rows from the latest-value reader on are `v: 2`; a
`v: 1` row with `stream: true` and a `stopReason` is the summed kind — halve its input, `cacheRead` and
`cacheCreation`.

## Policy

Ordered rules in the config's `policy` array. Each has a `rule` name, `on: "PreRequest"`, a `kind`,
a `level` — `suggest` (record the decision in the trace row, change nothing) or `block` (enforce it:
refuse the request, or apply the mutation) — and an optional `match` on `agent`, `origin`, `model` or
`path` (`*` is the only wildcard). A malformed rule fails at start, not mid-traffic.

| kind | fields | at `block` |
|---|---|---|
| `deny-model` | `models: [glob…]` | a request for a matching model gets a 403 |
| `require-identity` | — | a request with no `x-brain-*` headers gets a 403 |
| `budget` | `maxTokens`, `per: run\|agent\|item\|session` (default `run`) | once one identity key has spent `maxTokens` in this process's lifetime, a 429 |
| `append-system` | `text` | the text is appended as the last system block |
| `redact` | `patterns: [regex…]`, `replacement` (default `[redacted]`) | every match in the serialised body is replaced |

Ship a rule at `suggest`, read the trace, promote to `block` once you know what it would have done.
[`example.config.json`](../example.config.json) has one of each common shape; load it without
installing anything with `AK_GATEWAY_CONFIG=./example.config.json`.

Rules are evaluated in order and a mutation from an earlier rule applies even when a later rule
refuses the request — visible in the trace as `house-rules:appended,sonnet-is-off-limits:denied`.

### `append-system` appends, and the order is the whole point

Anthropic caches a **prefix**. Inserting text before a cached block invalidates every block after it,
so a one-line injection would silently turn each turn into a full-price cache miss. Appending after
the last block keeps the cached prefix byte-identical. Measured on a live probe:

```
   first call    in=20  out=52  cache_read=0       cache_write=142296
   second call   in=20  out=56  cache_read=127098  cache_write=15228    ← with a block appended
```

The cache survived the injection. That is the test that matters, not that the text arrived.
