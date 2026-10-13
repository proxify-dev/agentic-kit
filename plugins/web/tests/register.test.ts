import { describe, expect, test, tier } from 'claude-code/testing'

tier('user')

const TOOL = 'mcp__web__WebFetchRaw'

type Ran = { argv: readonly string[]; stdin?: string }

const exited = (exitCode: number, stdout: string, stderr: string) =>
  ({ value: { exitCode, stdout, stderr, isStdoutTruncated: false, isStderrTruncated: false } })

describe('web', () => {
  test('registers WebFetchRaw in the tool list, not behind ToolSearch', async ($, on) => {
    const registered: { name: string; isDeferred?: boolean; required?: unknown }[] = []
    on('session.start', ($, e) => ({ cwd: e.cwd }))
    on('tool.register', ($, e) => {
      registered.push({ name: e.name, isDeferred: e.isDeferred, required: e.inputSchema.required })
      return { value: { tool: `mcp__web__${e.name}` } }
    })
    await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
    expect(registered).toEqual([{ name: 'WebFetchRaw', isDeferred: false, required: ['url'] }])
  })

  test('a call runs the script with the input on stdin and answers its stdout', async ($, on) => {
    const runs: Ran[] = []
    on('process.run', ($, e) => {
      runs.push({ argv: e.argv, stdin: e.init?.stdin })
      return exited(0, 'ok: wrote 12 bytes to /tmp/a.md (markdown, text/html)\n', '')
    })
    const ran = await $.tool.call({ tool: TOOL, url: 'https://example.com', output_format: 'text' })
    expect(ran.result).toBe('ok: wrote 12 bytes to /tmp/a.md (markdown, text/html)')
    expect(runs).toHaveLength(1)
    expect(runs[0]?.argv[0]).toBe('uv')
    expect(runs[0]?.argv.at(-1)).toMatch(/\/scripts\/webfetch_raw\.py$/)
    expect(JSON.parse(runs[0]?.stdin ?? '')).toEqual({ url: 'https://example.com', output_format: 'text' })
  })

  test('a failed run is an error result carrying the script\'s stderr', async ($, on) => {
    on('process.run', () => exited(1, '', 'error: fetch failed: HTTP Error 404: Not Found\n'))
    const ran = await $.tool.call({ tool: TOOL, url: 'https://example.com/missing' })
    expect(ran.deny).toBe('error: fetch failed: HTTP Error 404: Not Found')
  })

  test('a script that cannot start is an error result saying so', async ($, on) => {
    on('process.run', () => ({ deny: 'uv: not found' }))
    const ran = await $.tool.call({ tool: TOOL, url: 'https://example.com' })
    expect(ran.deny).toContain('webfetch_raw.py did not run')
  })
})
