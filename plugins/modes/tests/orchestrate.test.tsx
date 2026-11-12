import { describe, expect, mock, test, tier } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On, SessionMessage } from 'claude-code'

tier('user')

const DAY = 24 * 3600 * 1000
const SUMMARY: SessionMessage = { role: 'user', text: 'The summary.', toolUses: [] }
const RULES = 'ORCHESTRATION MODE: ON. The rules, cut down.'
const LEAD_RULES = 'ORCHESTRATION MODE: LEAD. The lead rules, cut down.'

// No disk in a test: prompts/ cut down to the words the assertions read, frontmatter and {{rules}} kept, so the
// loader is tested too. The words themselves are checked live (README § Checks).
const PROMPTS: Record<string, string> = {
  '_rules.orchestrate.md': `---\nwhy: shared\n---\n${RULES}\n`,
  'switch-on.orchestrate.md': '---\nwhy: x\nfrom: [s]\n---\nOrchestration switched ON for this session. The rules:\n\n{{rules}}\n',
  'switch-off.orchestrate.md': '---\nwhy: x\n---\nOrchestration switched OFF for this session: the rules no longer apply.\n',
  'each-prompt.orchestrate.md': '---\nwhy: x\n---\nOrchestration is ON: one reminder line.\n',
  'after-compaction.orchestrate.md': '---\nwhy: x\n---\nOrchestration is ON (restated after compaction). The rules:\n\n{{rules}}\n',
  '_rules.orchestrate-lead.md': `---\nwhy: shared\n---\n${LEAD_RULES}\n`,
  'switch-on.orchestrate-lead.md': '---\nwhy: x\n---\nOrchestration switched to LEAD for this session. The rules:\n\n{{rules}}\n',
  'each-prompt.orchestrate-lead.md': '---\nwhy: x\n---\nOrchestration is LEAD: one reminder line.\n',
  'after-compaction.orchestrate-lead.md': '---\nwhy: x\n---\nOrchestration is LEAD (restated after compaction). The rules:\n\n{{rules}}\n',
}

/** The first line of a command's answer: the state; the lines under it explain it. */
const head = (text: string | undefined) => text?.split('\n')[0]

const registered: string[] = []
const hints: Record<string, string | undefined> = {}

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
    hints[e.name] = e.argumentHint
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
    expect(hints).toEqual({ orchestrate: '[on|lead|off|status]', ask: '[on|off|status]' })
  })

  test('on: the command row carries the rules once, then each prompt one reminder line', async ($, on) => {
    const carried = world(on)
    await start($)
    const ran = await run($, 'on')
    expect(head(ran.text)).toBe('Orchestration ON for this session.')
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
    expect(head(ran.text)).toBe('Orchestration OFF for this session.')
    expect(ran.context?.[0]).toContain('no longer apply')
    await submit($, 'after')
    expect(carried).toEqual([[]])
  })

  test('a bare command toggles; status and a repeat change nothing', async ($, on) => {
    world(on)
    await start($)
    expect(head((await run($, '')).text)).toBe('Orchestration ON for this session.')
    expect(head((await run($, 'status')).text)).toBe('Orchestration: ON, this session')
    const again = await run($, 'on')
    expect(head(again.text)).toBe('Orchestration is already ON for this session.')
    expect(again.context).toBeUndefined()
    expect(head((await run($, '')).text)).toBe('Orchestration OFF for this session.')
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

  test('lead: the command row carries the lead rules once, then each prompt the lead reminder', async ($, on) => {
    const carried = world(on)
    await start($)
    const ran = await run($, 'lead')
    expect(head(ran.text)).toBe('Orchestration ON (lead) for this session.')
    expect(ran.context?.[0]).toContain('Orchestration switched to LEAD')
    expect(ran.context?.[0]).toContain(LEAD_RULES)
    expect(ran.context?.[0]).not.toContain(RULES)
    expect(head((await run($, 'status')).text)).toBe('Orchestration: ON (lead), this session')
    await submit($, 'build it')
    expect(carried[0]).toEqual(['Orchestration is LEAD: one reminder line.'])
  })

  test('moving between on and lead sends the new level\'s rules; a repeat changes nothing', async ($, on) => {
    const carried = world(on)
    await start($)
    await run($, 'on')
    const up = await run($, 'lead')
    expect(head(up.text)).toBe('Orchestration ON (lead) for this session.')
    expect(up.context?.[0]).toContain(LEAD_RULES)
    const again = await run($, 'lead')
    expect(head(again.text)).toBe('Orchestration is already ON (lead) for this session.')
    expect(again.context).toBeUndefined()
    const down = await run($, 'on')
    expect(head(down.text)).toBe('Orchestration ON for this session.')
    expect(down.context?.[0]).toContain(RULES)
    await submit($, 'next')
    expect(carried[0]?.[0]).toMatch(/^Orchestration is ON:/)
  })

  test('off from lead: the one switch-off note, a bare command then turns plain on', async ($, on) => {
    const carried = world(on)
    await start($)
    await run($, 'lead')
    const off = await run($, '')
    expect(head(off.text)).toBe('Orchestration OFF for this session.')
    expect(off.context?.[0]).toContain('no longer apply')
    await submit($, 'after')
    expect(carried).toEqual([[]])
    expect(head((await run($, '')).text)).toBe('Orchestration ON for this session.')
  })

  test('each answer says what the state does from now on, and how to switch to the others', async ($, on) => {
    world(on)
    await start($)
    expect((await run($, 'lead')).text).toBe(
      [
        'Orchestration ON (lead) for this session.',
        '  Now:         Claude plans and decides; searches, reading, tests and planned edits go to agents',
        "  You'll see:  agents on haiku or sonnet in the background; a fork only when it needs the chat",
        '  Switch:      /orchestrate on — Claude works itself, hands off parts that stand alone',
        '               /orchestrate off — no delegation rules',
      ].join('\n'),
    )
    const off = (await run($, 'off')).text ?? ''
    expect(off).toContain('\n  Now:     Claude works as usual; agents already running may finish\n')
    expect(off).not.toContain("You'll see:")
    expect(off).toContain('/orchestrate lead — Claude plans and decides; agents do the work')
  })

  test('status lists every state with the one in force marked; a wrong argument lists them all', async ($, on) => {
    world(on)
    await start($)
    await run($, 'lead')
    expect((await run($, 'status')).text).toBe(
      [
        'Orchestration: ON (lead), this session',
        '    on    Claude works itself, hands off parts that stand alone',
        '  › lead  Claude plans and decides; agents do the work',
        '    off   no delegation rules',
        '  Switch: /orchestrate on | lead | off',
      ].join('\n'),
    )
    expect((await run($, 'maybe')).text).toBe(
      [
        "Unknown argument 'maybe'. /orchestrate takes one of:",
        '    on      Claude works itself, hands off parts that stand alone',
        '    lead    Claude plans and decides; agents do the work',
        '    off     no delegation rules',
        '    status  show which one is in force',
      ].join('\n'),
    )
  })

  test('at lead, a compaction restates the lead rules; a resumed session keeps its level', async ($, on) => {
    const carried = world(on, { 'orchestrate:s1': { at: 99 * DAY, level: 'lead' } })
    await start($)
    await submit($, 'resumed')
    expect(carried[0]).toEqual(['Orchestration is LEAD: one reminder line.'])
    await $.session.compact({ trigger: 'manual', messages: [SUMMARY] })
    await submit($, 'one')
    expect(carried[1]?.[0]).toContain('Orchestration is LEAD (restated after compaction)')
    expect(carried[1]?.[0]).toContain(LEAD_RULES)
  })

  test('a stored level the mode no longer has reads as plain on', async ($, on) => {
    const carried = world(on, { 'orchestrate:s1': { at: 99 * DAY, level: 'gone' } })
    await start($)
    await submit($, 'resumed')
    expect(carried[0]?.[0]).toMatch(/^Orchestration is ON:/)
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
    await run($, 'lead')
    for (const footer of footers) await footer.redraw()
    expect(last).toEqual({ terminal: 'focus & orchestrating · lead', desktop: 'focus & orchestrating · lead' })
    await run($, 'off')
    for (const footer of footers) await footer.redraw()
    expect(last).toEqual({ terminal: 'focus', desktop: 'focus' })
  })
})
