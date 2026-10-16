import { describe, expect, mock, test, tier } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

tier('user')

const RULES = 'ASK MODE: ON. The rules, cut down.'
const REFUSED = 'Refused: ask mode is ON for this session.'

// No disk in a test: prompts/ cut down to the words the assertions read, frontmatter and {{rules}} kept.
const PROMPTS: Record<string, string> = {
  '_rules.ask.md': `---\nwhy: shared\n---\n${RULES}\n`,
  'switch-on.ask.md': '---\nwhy: x\n---\nAsk mode switched ON for this session. The rules:\n\n{{rules}}\n',
  'switch-off.ask.md': '---\nwhy: x\n---\nAsk mode switched OFF for this session: the rules no longer apply.\n',
  'each-prompt.ask.md': '---\nwhy: x\n---\nAsk mode is ON: one reminder line.\n',
  'after-compaction.ask.md': '---\nwhy: x\n---\nAsk mode is ON (restated after compaction).\n\n{{rules}}\n',
  'before-edit.ask.md': `---\nwhy: x\n---\n${REFUSED}\n`,
  '_rules.orchestrate.md': '---\nwhy: x\n---\nORCHESTRATION MODE: ON.\n',
  'switch-on.orchestrate.md': '---\nwhy: x\n---\nOrchestration switched ON.\n\n{{rules}}\n',
  'each-prompt.orchestrate.md': '---\nwhy: x\n---\nOrchestration is ON: one reminder line.\n',
}

/** The engine beneath the plugin: one session, a store, a clock, prompts/, and tools that run; returns each prompt's context. */
function world(on: On) {
  mock.store(on, {})
  mock.clock(on, { now: 1000 })
  on('fs.read', ($, e, next) => {
    const name = e.path.match(/\/prompts\/([^/]+)$/)?.[1]
    const text = name === undefined ? undefined : PROMPTS[name]
    return text === undefined ? next(e) : { value: text }
  })
  on('session.id', () => ({ value: 's1' }))
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('tool.call', () => ({ result: 'ran' }))
  const carried: (readonly string[])[] = []
  on('prompt.submit', ($, e) => {
    carried.push(e.context ?? [])
    return { text: e.text, context: e.context }
  })
  return carried
}

const start = ($: Engine) => $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })
const run = ($: Engine, command: string, args: string) =>
  $.command.run({ command, args, origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 120 } })
const submit = ($: Engine, text: string) => $.prompt.submit({ text, wait: false, origin: { kind: 'composer' } })
const edit = ($: Engine) => $.tool.call({ tool: 'Edit', file_path: '/work/a.ts', old_string: '1', new_string: '2' })

describe('ask', () => {
  test('on: the command row carries the rules once, then each prompt one reminder line', async ($, on) => {
    const carried = world(on)
    await start($)
    const ran = await run($, 'ask', 'on')
    expect(ran.text).toBe('Ask mode ON for this session.')
    expect(ran.context?.[0]).toContain(RULES)
    expect(ran.context?.[0]).not.toMatch(/why:|---|\{\{rules\}\}/)
    await submit($, 'how does this work?')
    expect(carried).toEqual([['Ask mode is ON: one reminder line.']])
  })

  test('while on, Edit and Write are refused with the reason; Read and Bash run', async ($, on) => {
    world(on)
    await start($)
    expect((await edit($)).result).toBe('ran')
    await run($, 'ask', 'on')
    expect((await edit($)).deny).toBe(REFUSED)
    expect((await $.tool.call({ tool: 'Write', file_path: '/work/b.ts', content: 'x' })).deny).toBe(REFUSED)
    expect((await $.tool.call({ tool: 'Read', file_path: '/work/a.ts' })).result).toBe('ran')
    expect((await $.tool.call({ tool: 'Bash', command: 'git status' })).result).toBe('ran')
  })

  test('off: the row says the rules no longer apply, edits run again, prompts carry nothing', async ($, on) => {
    const carried = world(on)
    await start($)
    await run($, 'ask', 'on')
    const ran = await run($, 'ask', 'off')
    expect(ran.text).toBe('Ask mode OFF for this session.')
    expect(ran.context?.[0]).toContain('no longer apply')
    expect((await edit($)).result).toBe('ran')
    await submit($, 'after')
    expect(carried).toEqual([[]])
  })

  test('with /orchestrate on too, a prompt carries both reminders and the footer both chips', async ($, on) => {
    const carried = world(on)
    let footer = ''
    on('ui.render', { component: 'SessionMode' }, ($, e) => {
      footer = e.props.modes.join(' & ')
      const { Text } = $.ui.resolve(e)
      return <Text>{footer}</Text>
    })
    await start($)
    const mounted = await $.ui.mount({ plugin: 'modes', surface: 'terminal', component: 'SessionMode', props: { modes: [] } })
    await run($, 'orchestrate', 'on')
    await run($, 'ask', 'on')
    await mounted.redraw()
    expect(footer).toBe('orchestrating & ask mode')
    await submit($, 'both')
    expect(carried).toEqual([['Orchestration is ON: one reminder line.', 'Ask mode is ON: one reminder line.']])
  })
})
