// A session mode: /<name> [on|off|status], what Claude is told about it, and its chip. /orchestrate and
// /ask are each a ModeSpec, registered together by modes(on, specs) from the plugin's one hooks module, register.ts.
//
// A plugin can add to the conversation but never take back, so each switch is written in once, where it
// happens, and the history says what is in force at every point:
//
//   /<name> on | off           the command's own row, no model call; its hidden note is the rules (on) or
//                              "they no longer apply" (off)
//   each prompt while on       one reminder line, attached to the prompt
//   the prompt after a         the full rules again in place of the reminder: a summary can drop them
//   compaction, while on
//   the prompt footer          the mode's chip among the mode labels while on
//
// The words are prompts/<moment>.<name>.md, read when they are sent: the file name says when Claude reads
// it, the frontmatter why, and {{rules}} stands for prompts/_rules.<name>.md. Nothing to edit here for them.
//
// The state is $.state for this process (one entry per mode, by name, in modes.modes), and $.store (key <name>:<session id>, present only while on) so
// `claude --resume` finds it; an entry untouched for 90 days is removed at session start.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, On } from 'claude-code'

import type { ModeState } from '../types'

const STALE_MS = 90 * 24 * 3600 * 1000

type Saved = { at: number }

export type ModeSpec = {
  /** The command, the prompts/ suffix and the store prefix: `orchestrate` is /orchestrate. */
  name: string
  /** How the command's answers name it: `Orchestration` gives "Orchestration ON for this session." */
  label: string
  /** The prompt footer's mode label while on. */
  chip: string
  description: string
}

/** A prompts/ file's body: its frontmatter (why, from) is for people and never sent. */
async function body($: EngineInterface, file: string): Promise<string> {
  const text = await $.fs.read(`${$.plugin.root}/prompts/${file}.md`)
  return text.replace(/^---\n[\s\S]*?\n---\n/, '').trim()
}

/** What Claude reads at MOMENT of mode NAME: prompts/<moment>.<name>.md, {{rules}} from prompts/_rules.<name>.md. */
export async function words($: EngineInterface, name: string, moment: string): Promise<string> {
  const text = await body($, `${moment}.${name}`)
  return text.includes('{{rules}}') ? text.replace('{{rules}}', await body($, `_rules.${name}`)) : text
}

const MODES = atom({ plugin: 'modes', key: 'modes' } as const, {})

/** Rewrites mode SPEC's entry in MODES; none is left out when it is undefined. */
async function set($: EngineInterface, spec: ModeSpec, to: (s: ModeState | undefined) => ModeState | undefined) {
  await update($, MODES, modes => ({ ...modes, [spec.name]: to(modes[spec.name]) }))
}

const storeKey = (spec: ModeSpec, sessionId: string) => `${spec.name}:${sessionId}`

/** Mode SPEC's state in this session, loaded from the store when the held value is another session's or none. */
async function current($: EngineInterface, spec: ModeSpec): Promise<ModeState> {
  const sessionId = await $.session.id()
  const held = (await read($, MODES))[spec.name]
  if (held?.sessionId === sessionId) return held
  const loaded: ModeState = {
    sessionId,
    isOn: (await $.store.get(storeKey(spec, sessionId))) !== undefined,
    needsRules: false,
  }
  await set($, spec, () => loaded)
  return loaded
}

/** Whether mode SPEC is on in this session: for a mode's own hooks beside the switch (ask.ts's refusal). */
export async function isOn($: EngineInterface, spec: ModeSpec): Promise<boolean> {
  return (await current($, spec)).isOn
}

async function switchTo($: EngineInterface, spec: ModeSpec, s: ModeState, turnOn: boolean) {
  await set($, spec, () => ({ ...s, isOn: turnOn, needsRules: false }))
  const key = storeKey(spec, s.sessionId)
  if (turnOn) await $.store.set(key, { at: await $.clock.now() } satisfies Saved)
  else await $.store.delete(key)
}

async function sweep($: EngineInterface, spec: ModeSpec) {
  const now = await $.clock.now()
  for (const key of await $.store.keys()) {
    if (!key.startsWith(`${spec.name}:`)) continue
    const saved = (await $.store.get(key)) as Partial<Saved> | undefined
    if (typeof saved?.at !== 'number' || now - saved.at > STALE_MS) await $.store.delete(key)
  }
}

const commandOf = (spec: ModeSpec) => new RegExp(`^/(modes:)?${spec.name}(\\s|$)`)

/** Registers the hooks of every mode in SPECS, once each: the commands, the notes Claude reads, the chips. */
export function modes(on: On, specs: readonly ModeSpec[]) {
  on('session.start', async ($, e, next) => {
    for (const spec of specs) {
      await $.command.register({ name: spec.name, description: spec.description, argumentHint: '[on|off|status]' })
      await sweep($, spec)
      await current($, spec)
    }
    return next(e)
  })

  on('command.run', async ($, e, next) => {
    const spec = specs.find(s => s.name === e.command)
    if (spec === undefined) return next(e)
    const s = await current($, spec)
    const arg = e.args.trim().split(/\s+/)[0] ?? ''
    if (arg === 'status') return { text: `${spec.label}: ${s.isOn ? 'ON' : 'OFF'} (this session)` }
    if (arg !== '' && arg !== 'on' && arg !== 'off') {
      return { text: `Unknown argument '${arg}'. Use: /${spec.name} [on|off|status]` }
    }
    const turnOn = arg === '' ? !s.isOn : arg === 'on'
    if (turnOn === s.isOn) return { text: `${spec.label} is already ${turnOn ? 'ON' : 'OFF'} for this session.` }
    const note = await words($, spec.name, turnOn ? 'switch-on' : 'switch-off')
    await switchTo($, spec, s, turnOn)
    return { text: `${spec.label} ${turnOn ? 'ON' : 'OFF'} for this session.`, context: [note] }
  })

  on('prompt.submit', async ($, e, next) => {
    if (specs.some(spec => commandOf(spec).test(e.text))) return next(e)
    const notes: string[] = []
    for (const spec of specs) {
      const s = await current($, spec)
      if (!s.isOn) continue
      notes.push(await words($, spec.name, s.needsRules ? 'after-compaction' : 'each-prompt'))
      if (s.needsRules) await set($, spec, v => (v ? { ...v, needsRules: false } : v))
    }
    if (notes.length === 0) return next(e)
    return next({ ...e, context: [...(e.context ?? []), ...notes] })
  }).catch(($, e, next) => next(e))

  on('session.compact', async ($, e, next) => {
    const done = await next(e)
    if (e.trigger === 'precompute' || e.agentId !== undefined || done.skip !== undefined) return done
    for (const spec of specs) {
      if (await isOn($, spec)) await set($, spec, v => (v ? { ...v, needsRules: true } : v))
    }
    return done
  }).catch(($, e, next) => next(e))

  on('session.end', async ($, e, next) => {
    await update($, MODES, () => ({}))
    return next(e)
  })

  on('ui.render', { component: 'SessionMode' }, async ($, e, next) => {
    const held = await read($, MODES)
    const chips = specs.filter(spec => held[spec.name]?.isOn).map(spec => spec.chip)
    if (chips.length === 0) return next(e)
    return next({ ...e, props: { ...e.props, modes: [...e.props.modes, ...chips] } })
  })
}
