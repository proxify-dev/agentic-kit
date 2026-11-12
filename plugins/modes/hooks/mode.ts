// A session mode: /<name> [on|<level>|off|status], what Claude is told about it, and its chip. /orchestrate
// and /ask are each a ModeSpec, registered together by modes(on, specs) from the plugin's one hooks module,
// register.ts.
//
// A plugin can add to the conversation but never take back, so each switch is written in once, where it
// happens, and the history says what is in force at every point:
//
//   /<name> on | <level> | off  the command's own row, no model call; its hidden note is the rules (on, or a
//                               level) or "they no longer apply" (off)
//   each prompt while on        one reminder line, attached to the prompt
//   the prompt after a          the full rules again in place of the reminder: a summary can drop them
//   compaction, while on
//   the prompt footer           the mode's chip (or its level's) among the mode labels while on
//
// The words are prompts/<moment>.<name>.md, read when they are sent: the file name says when Claude reads
// it, the frontmatter why, and {{rules}} stands for prompts/_rules.<name>.md. A level reads its own set,
// prompts/<moment>.<name>-<level>.md, except switch-off, which is the mode's one file for every level.
// Nothing to edit here for them.
//
// The state is $.state for this process (one entry per mode, by name, in modes.modes), and $.store (key
// <name>:<session id>, { at, level? }, present only while on) so `claude --resume` finds it; an entry
// untouched for 90 days is removed at session start.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, On } from 'claude-code'

import type { ModeState } from '../types'

const STALE_MS = 90 * 24 * 3600 * 1000

type Saved = { at: number; level?: string }

export type ModeSpec = {
  /** The command, the prompts/ suffix and the store prefix: `orchestrate` is /orchestrate. */
  name: string
  /** How the command's answers name it: `Orchestration` gives "Orchestration ON for this session." */
  label: string
  /** The prompt footer's mode label while on. */
  chip: string
  description: string
  /** What the person reads about plain `on` and about `off`, under each answer of the command. */
  about: { on: About; off: About }
  /**
   * Other ways to be on, each its own argument: `/orchestrate lead` is on at level `lead`, told the rules in
   * prompts/<moment>.orchestrate-lead.md and shown in the footer as its own chip. Plain `on` is no level.
   */
  levels?: Record<string, { chip: string; about: About }>
}

/**
 * One state as the person reads it (the command's answer is a transcript line Claude reads too). Words for
 * the person, never rules for Claude: those are prompts/.
 */
export type About = {
  /** A few words for the list of states: `Claude plans and decides; agents do the work`. */
  short: string
  /** What Claude does from now on, one line under the switch's answer (keep it under ~90 characters). */
  now: string
  /** What the person will notice, one line; left out when nothing new shows. */
  see?: string
}

/** On, and at which level; `level` undefined is plain `on`. */
type Target = { isOn: boolean; level?: string }

/** The argument that names state T: `on`, `off` or the level. */
const argOfTarget = (t: Target) => (!t.isOn ? 'off' : (t.level ?? 'on'))

/** Every state of SPEC the person can switch to, in the order on, levels, off. */
function statesOf(spec: ModeSpec): { arg: string; about: About }[] {
  const levels = Object.entries(spec.levels ?? {}).map(([arg, l]) => ({ arg, about: l.about }))
  return [{ arg: 'on', about: spec.about.on }, ...levels, { arg: 'off', about: spec.about.off }]
}

/**
 * HEAD, then labelled lines a terminal keeps short: what Claude does now, what you will see, and each other
 * state to switch to, one per line.
 */
function explained(spec: ModeSpec, t: Target, head: string): string {
  const arg = argOfTarget(t)
  const states = statesOf(spec)
  const about = states.find(s => s.arg === arg)?.about
  const rows: [string, string][] = []
  if (about) rows.push(['Now:', about.now])
  if (about?.see) rows.push(["You'll see:", about.see])
  states.filter(s => s.arg !== arg).forEach((s, i) => rows.push([i === 0 ? 'Switch:' : '', `/${spec.name} ${s.arg} — ${s.about.short}`]))
  const width = Math.max(...rows.map(([label]) => label.length))
  return [head, ...rows.map(([label, text]) => `  ${label.padEnd(width)}  ${text}`)].join('\n')
}

/** Every state of SPEC, one per line, the one in force marked; EXTRA rows (`status`) aligned with them. */
function stateList(spec: ModeSpec, current: Target | undefined, extra: { arg: string; short: string }[] = []): string {
  const rows = [...statesOf(spec).map(s => ({ arg: s.arg, short: s.about.short })), ...extra]
  const width = Math.max(...rows.map(s => s.arg.length))
  const mark = current === undefined ? undefined : argOfTarget(current)
  return rows.map(s => `  ${s.arg === mark ? '›' : ' '} ${s.arg.padEnd(width)}  ${s.short}`).join('\n')
}

/** The prompts/ suffix of SPEC at LEVEL: `orchestrate`, or `orchestrate-lead` for level `lead`. */
const promptsOf = (spec: ModeSpec, level: string | undefined) => (level === undefined ? spec.name : `${spec.name}-${level}`)

/** How the command's answers name a state: OFF, ON, or ON (lead). */
const shown = (t: Target) => (!t.isOn ? 'OFF' : t.level === undefined ? 'ON' : `ON (${t.level})`)

/** The arguments SPEC takes, in the order the hint and the error list them. */
const argsOf = (spec: ModeSpec) => ['on', ...Object.keys(spec.levels ?? {}), 'off', 'status']

/** What ARG asks SPEC to become from state S; undefined for an argument it does not take. */
function targetOf(spec: ModeSpec, s: ModeState, arg: string): Target | undefined {
  if (arg === '') return s.isOn ? { isOn: false } : { isOn: true }
  if (arg === 'on') return { isOn: true }
  if (arg === 'off') return { isOn: false }
  return Object.hasOwn(spec.levels ?? {}, arg) ? { isOn: true, level: arg } : undefined
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
  const saved = (await $.store.get(storeKey(spec, sessionId))) as Partial<Saved> | undefined
  const level = typeof saved?.level === 'string' && Object.hasOwn(spec.levels ?? {}, saved.level) ? saved.level : undefined
  const loaded: ModeState = { sessionId, isOn: saved !== undefined, needsRules: false, ...(level ? { level } : {}) }
  await set($, spec, () => loaded)
  return loaded
}

/** Whether mode SPEC is on in this session: for a mode's own hooks beside the switch (ask.ts's refusal). */
export async function isOn($: EngineInterface, spec: ModeSpec): Promise<boolean> {
  return (await current($, spec)).isOn
}

async function switchTo($: EngineInterface, spec: ModeSpec, s: ModeState, to: Target) {
  const level = to.isOn ? to.level : undefined
  await set($, spec, () => ({ sessionId: s.sessionId, isOn: to.isOn, needsRules: false, ...(level ? { level } : {}) }))
  const key = storeKey(spec, s.sessionId)
  if (to.isOn) await $.store.set(key, { at: await $.clock.now(), ...(level ? { level } : {}) } satisfies Saved)
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
      await $.command.register({ name: spec.name, description: spec.description, argumentHint: `[${argsOf(spec).join('|')}]` })
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
    if (arg === 'status') {
      return { text: `${spec.label}: ${shown(s)}, this session\n${stateList(spec, s)}\n  Switch: /${spec.name} ${argsOf(spec).filter(a => a !== 'status').join(' | ')}` }
    }
    const to = targetOf(spec, s, arg)
    if (to === undefined) {
      const status = { arg: 'status', short: 'show which one is in force' }
      return { text: `Unknown argument '${arg}'. /${spec.name} takes one of:\n${stateList(spec, undefined, [status])}` }
    }
    if (to.isOn === s.isOn && (!to.isOn || to.level === s.level)) {
      return { text: explained(spec, to, `${spec.label} is already ${shown(to)} for this session.`) }
    }
    // Moving between levels sends the new level's switch-on: its words say they replace the earlier rules.
    const note = await words($, promptsOf(spec, to.level), to.isOn ? 'switch-on' : 'switch-off')
    await switchTo($, spec, s, to)
    return { text: explained(spec, to, `${spec.label} ${shown(to)} for this session.`), context: [note] }
  })

  on('prompt.submit', async ($, e, next) => {
    if (specs.some(spec => commandOf(spec).test(e.text))) return next(e)
    const notes: string[] = []
    for (const spec of specs) {
      const s = await current($, spec)
      if (!s.isOn) continue
      notes.push(await words($, promptsOf(spec, s.level), s.needsRules ? 'after-compaction' : 'each-prompt'))
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
    const chips = specs.flatMap(spec => {
      const s = held[spec.name]
      if (!s?.isOn) return []
      return [s.level === undefined ? spec.chip : (spec.levels?.[s.level]?.chip ?? spec.chip)]
    })
    if (chips.length === 0) return next(e)
    return next({ ...e, props: { ...e.props, modes: [...e.props.modes, ...chips] } })
  })
}
