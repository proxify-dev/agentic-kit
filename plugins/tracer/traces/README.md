# traces

The tool-call index: one row per tool_use_id from every session, in `traces.db`. The tracer's
Stop hook runs `index` after every turn; `ak trace` reads it. One
committed bundle, `dist/traces.mjs`, is what runs: node >= 22.13, no `npm install`, no build, no
shell, so a tracer installed on its own carries its indexer.

Why it exists: without it, "which tool calls failed in the last hour, in any session?" means scanning every
transcript, and an agent doing that spends many calls and much of its context. With it, the same question is one
indexed query: `tracer trace --outcome error --since 1h`.

| entry | what |
|---|---|
| `node plugins/tracer/traces/bin/traces.mjs <verb>` | `index [--recent]`, and `list · get · stats · purge`. Runs from the home directory, so every caller shares the one home trace store |

The same command works on macOS, Linux and Windows. The entry (through `bin/_launch.mjs`, which
the app's `brain-mcp.mjs` shares) checks the node floor (exit 127), finds `dist/traces.mjs`
(exit 66 when it is missing) and imports it; stdout is the one JSON document, every diagnostic
is one line on stderr. Set the kit's `TRACES_DEBUG` env var (with the brand prefix) to see
which node and bundle ran. The node ≥ 22.13 lookup for callers whose PATH has none new enough
is Python, in the hook (`hooks/convo-index-stop.py`) and the CLI (`src/tracer/calls.py`);
`bin/traces` is the older bash door, which uses `bin/find-node.sh`.

Build: `npm run build` here (writes `dist/`, committed), then a tracer release; the app's
`npm run selftest:bundle-fresh` fails when `dist/` lags the source. Dev: `npm run link:dev`
links `node_modules` to the app's (for esbuild).
