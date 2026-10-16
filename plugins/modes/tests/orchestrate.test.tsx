import { describe, expect, mock, test, tier } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On, SessionMessage } from 'claude-code'

tier('user')

const DAY = 24 * 3600 * 1000
const SUMMARY: SessionMessage = { role: 'user', text: 'The summary.', toolUses: [] }
const RULES = 'ORCHESTRATION MODE: ON. The rules, cut down.'

// No disk in a test: prompts/ cut down to the words the assertions read, frontmatter and {{rules}} kept, so the
// loader is tested too. The words themselves are checked live (README § Checks).
const PROMPTS: Record<string, string> = {
  '_rules.orchestrate.md': `---\nwhy: shared\n---\n${RULES}\n`,
  'switch-on.orchestrate.md': '---\nwhy: x\nfrom: [s]\n---\nOrchestration switched ON for this session. The rules:\n\n{{rules}}\n',
  'switch-off.orchestrate.md': '---\nwhy: x\n---\nOrchestration switched OFF for this session: the rules no longer apply.\n',
  'each-prompt.orchestrate.md': '---\nwhy: x\n---\nOrchestration is ON: one reminder line.\n',
  'after-compaction.orchestrate.md': '---\nwhy: x\n---\nOrchestration is ON (restated after compaction). The rules:\n\n{{rules}}\n',
}

const registered: string[] = []

/** The engine beneath the plugin: one session, a store, a clock, prompts/; returns the context each prompt carried. */
function world(on: On, store: Record<string, unknown> = {}, now = 100 * DAY) {
  mock.store(on, store)
  mock.clock(on, { now })
  on('fs.read', ($, e, next) => {
    const name = e.path.match(/\/prompts\/([^/]+)$/)?.[1]
    const text = name === undefined ? undefined : PROMPTS[name]
    return text === undefined ? next(e) : { value: text }
  })
  on('session.id', () => ({ value: 's1' }))
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('command.register', ($, e) => {
    registered.push(e.name)
    return { value: { command: e.name } }
  })
  on('session.compact', () => ({ messages: [SUMMARY] }))
  const carried: (readonly string[])[] = []
  on('prompt.submit', ($, e) => {
    carried.push(e.context ?? [])
    return { text: e.text, context: e.context }
  })
  return carried
}

const start = ($: Engine) => $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
const run = ($: Engine, args: string) =>
  $.command.run({ command: 'orchestrate', args, origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 120 } })
const submit = ($: Engine, text: string) => $.prompt.submit({ text, wait: false, origin: { kind: 'composer' } })

describe('orchestrate', () => {
  test('registers /orchestrate and /ask at session start', async ($, on) => {
    registered.length = 0
    world(on)
    await start($)
    expect(registered).toEqual(['orchestrate', 'ask'])
  })

  test('on: the command row carries the rules once, then each prompt one reminder line', async ($, on) => {
    const carried = world(on)
    await start($)
    const ran = await run($, 'on')
    expect(ran.text).toBe('Orchestration ON for this session.')
    expect(ran.context?.[0]).toContain('Orchestration switched ON')
    expect(ran.context?.[0]).toContain(RULES)
    expect(ran.context?.[0]).not.toMatch(/why:|from:|---|\{\{rules\}\}/)
    await submit($, 'build it')
    await submit($, 'next')
    expect(carried).toHaveLength(2)
    for (const context of carried) {
      expect(context).toHaveLength(1)
      expect(context[0]).toMatch(/^Orchestration is ON:/)
      expect(context[0]).not.toContain(RULES)
    }
  })

  test('off: the command row says the rules no longer apply, and prompts carry nothing', async ($, on) => {
    const carried = world(on)
    await start($)
    await run($, 'on')
    const ran = await run($, 'off')
    expect(ran.text).toBe('Orchestration OFF for this session.')
    expect(ran.context?.[0]).toContain('no longer apply')
    await submit($, 'after')
    expect(carried).toEqual([[]])
  })

  test('a bare command toggles; status and a repeat change nothing', async ($, on) => {
    world(on)
    await start($)
    expect((await run($, '')).text).toBe('Orchestration ON for this session.')
    expect((await run($, 'status')).text).toBe('Orchestration: ON (this session)')
    const again = await run($, 'on')
    expect(again.text).toBe('Orchestration is already ON for this session.')
    expect(again.context).toBeUndefined()
    expect((await run($, '')).text).toBe('Orchestration OFF for this session.')
    expect((await run($, 'maybe')).text).toContain("Unknown argument 'maybe'")
  })

  test('after a compaction, the next prompt carries the full rules again, then reminders', async ($, on) => {
    const carried = world(on)
    await start($)
    await run($, 'on')
    await $.session.compact({ trigger: 'manual', messages: [SUMMARY] })
    await submit($, 'one')
    await submit($, 'two')
    expect(carried[0]?.[0]).toContain('restated after compaction')
    expect(carried[0]?.[0]).toContain(RULES)
    expect(carried[1]?.[0]).toMatch(/^Orchestration is ON:/)
  })

  test('a resumed session that was on is on: reminders, no second copy of the rules', async ($, on) => {
    const carried = world(on, { 'orchestrate:s1': { at: 99 * DAY } })
    await start($)
    await submit($, 'resumed')
    expect(carried[0]?.[0]).toMatch(/^Orchestration is ON:/)
  })

  test('a store entry untouched for 90 days is removed at session start', async ($, on) => {
    const carried = world(on, { 'orchestrate:s1': { at: 1 } })
    await start($)
    await submit($, 'resumed')
    expect(carried).toEqual([[]])
  })

  test('the prompt footer shows "orchestrating" among the mode labels while on', async ($, on) => {
    world(on)
    const last: Record<string, string> = {}
    on('ui.render', { component: 'SessionMode' }, ($, e) => {
      last[e.surface] = e.props.modes.join(' & ')
      const { Text } = $.ui.resolve(e)
      return <Text dimColor>{e.props.modes.join(' & ')}</Text>
    })
    await start($)
    const footers = []
    for (const surface of ['terminal', 'desktop'] as const) {
      footers.push(await $.ui.mount({ plugin: 'modes', surface, component: 'SessionMode', props: { modes: ['focus'] } }))
    }
    expect(last).toEqual({ terminal: 'focus', desktop: 'focus' })
    await run($, 'on')
    for (const footer of footers) await footer.redraw()
    expect(last).toEqual({ terminal: 'focus & orchestrating', desktop: 'focus & orchestrating' })
    await run($, 'off')
    for (const footer of footers) await footer.redraw()
    expect(last).toEqual({ terminal: 'focus', desktop: 'focus' })
  })
})
