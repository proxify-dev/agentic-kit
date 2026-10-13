import type { Register } from 'claude-code'

// The tool is declared here and served by scripts/webfetch_raw.py, run once per call. Not in this module:
// it has no npm and no Node for an HTML extractor, and $.http.fetch hands back text only (a PDF would not
// survive), so the fetch, the extraction (trafilatura) and the write are the script's.
const NAME = 'WebFetchRaw'

const DESCRIPTION = `Fetch a URL and save the page's own content to a file. No model reads it on the way.

WebFetch passes the page to a small model and returns that model's answer about it, which is a paraphrase. This tool writes the page itself to a file and returns one line naming the file; Read the file after, whole or in parts (offset/limit). Use it when you need the exact words, code, numbers or links on a page. Use WebFetch to ask a question about a page.

An HTML page is extracted by trafilatura (the main content, without navigation and footer) into output_format. Any other content (JSON, plain text, markdown, a PDF) is written byte for byte, and so is an HTML page with output_format "raw".`

const INPUT_SCHEMA = {
  type: 'object',
  properties: {
    url: { type: 'string', description: 'The http:// or https:// URL to fetch.' },
    path: {
      type: 'string',
      description: 'Absolute path of the file to write; missing folders are created. Left out, the file is written to <tmp>/webfetch-raw/<host>/<time>-<slug>-<hash>.<ext>.',
    },
    output_format: {
      type: 'string',
      enum: ['markdown', 'text', 'html', 'raw'],
      default: 'markdown',
      description: 'For an HTML page: markdown, text, html (the main content as cleaned HTML), or raw (the body exactly as the server sent it).',
    },
    include_links: { type: 'boolean', default: true, description: 'Keep link targets in the extracted content.' },
    include_images: { type: 'boolean', default: false, description: 'Keep image references in the extracted content.' },
    include_tables: { type: 'boolean', default: true, description: 'Keep tables in the extracted content.' },
    include_comments: { type: 'boolean', default: false, description: "Keep the page's comment section in the extracted content." },
    timeout: { type: 'integer', minimum: 1, maximum: 300, default: 30, description: 'Seconds to wait for the server.' },
    overwrite: { type: 'boolean', default: true, description: 'false refuses to replace a file already at path.' },
  },
  required: ['url'],
  additionalProperties: false,
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    // isDeferred false: the schema sits in the tool list beside WebFetch, so the model reaches for it
    await $.tool.register({ name: NAME, description: DESCRIPTION, inputSchema: INPUT_SCHEMA, isDeferred: false })
    return started
  })

  // The hook IS the tool: no next(e), it answers the call itself.
  on('tool.call', { tool: 'mcp__web__WebFetchRaw' }, async ($, e) => {
    const { tool, tool_use_id, consent, agentId, requestMeta, ...input } = e as Record<string, unknown>
    const root = $.plugin.root
    const seconds = typeof input.timeout === 'number' ? input.timeout : 30
    try {
      const ran = await $.process.run(
        ['uv', 'run', '--quiet', '--frozen', '--no-dev', '--project', root, 'python', `${root}/scripts/webfetch_raw.py`],
        // the server's wait, plus room for uv to build the environment on a first call
        { stdin: JSON.stringify(input), timeoutMs: Math.min((seconds + 90) * 1000, 600_000) },
      )
      if (ran.exitCode === 0) return { result: ran.stdout.trim() }
      return { deny: ran.stderr.trim() || `webfetch_raw.py exited ${ran.exitCode}` }
    } catch (err) {
      return { deny: `error: webfetch_raw.py did not run: ${err instanceof Error ? err.message : String(err)}` }
    }
  }).catch(() => ({ deny: `error: the ${NAME} hook failed (claude --debug says why)` }))
}
