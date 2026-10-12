# Security model

- **Localhost only, no key.** It binds `127.0.0.1`. Being on the machine is the credential, so the
  front door refuses what a web page in your browser would send: a cross-site `Sec-Fetch-Site`, an
  `Origin`, or a `Host` that does not name this machine gets a 403 before the body is read
  ([control-api.md](control-api.md#who-may-knock)).
- **Tokens stay in one file.** `accounts.json` is written 0600 by temp file and rename (an owner-only
  ACL on Windows). A launched claude gets a placeholder token, never a real one, and no inherited
  `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`.
- **Headers are never written to disk.** No code path in the trace accepts them, and a test asserts
  it. The gateway's own `x-brain-*` identity headers are stripped before a request leaves the machine.
- **The trace holds prompt content.** System prompts and tool lists are kept; at `gaps`, whole bodies
  for calls no transcript records. Bodies expire after `blobDays` (7). Set `capture: meta` or `none`
  to keep less ([policy-and-trace.md](policy-and-trace.md#trace)).

## What still goes around it

Every *model* call passes through; not every call. Claude Code 2.1.281 with its base URL pointed
here, measured 2026-09-24 through a proxy that answered locally:

```
   call                                  goes to              carries
   GET  /api/claude_cli/bootstrap        api.anthropic.com    the Keychain OAuth login — the real token, not the gateway's placeholder
   GET  /api/claude_code_penguin_mode    api.anthropic.com    the Keychain OAuth login
   GET  /mcp-registry/v0/servers         api.anthropic.com    nothing
   POST /api/event_logging/v2/batch      api.anthropic.com    ~230 metadata events (ids, counts, lengths, hashes)
   POST /api/v2/logs                     Datadog              the same kind of metadata
   HEAD /api/hello                       here                 a preconnect, without the custom headers
```

A marker prompt appeared in none of them. Routing removes the credential from the *environment*, not
from the machine. `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` stops every direct call and changes the
prompt with it — an interactive session loses the Artifact and SendFeedback tools, ≈9.1K tokens;
`DISABLE_TELEMETRY` and `DISABLE_GROWTHBOOK` change five sections through feature flags.
