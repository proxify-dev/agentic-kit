import type { EngineInterface, Register } from 'claude-code'

// What each command writes, relative to the session's working directory; the template it starts from, in this
// plugin; and the skill that designs one with Claude.
const KINDS = {
  'new-agent': { noun: 'agent', title: 'New agent', path: (name: string) => `.claude/agents/${name}.md`, when: 'when Claude should hand work to it', template: 'skills/agent-development/_template.md', skill: 'agentic-engineering:agent-development', example: 'code-reviewer', sample: 'Reviews diffs for bugs, after each change' },
  'new-skill': { noun: 'skill', title: 'New skill', path: (name: string) => `.claude/skills/${name}/SKILL.md`, when: 'when Claude should use it', template: 'skills/skill-development/_template.md', skill: 'agentic-engineering:skill-development', example: 'release-notes', sample: 'Writes the notes for a version' },
} as const
type Command = keyof typeof KINDS
const isCommand = (id: string): id is Command => id in KINDS
// Every surface but mobile draws Input, which the form needs.
const drawsInput = <E extends { surface: string }>(e: E): e is E & { surface: 'terminal' | 'desktop' | 'vscode' } => e.surface !== 'mobile'

// What the form holds while its pane is open.
type Form = { name: string; what: string; error: string }
// The module's state: whether a user is at the session, each command's form, how many times a form has
// opened, and the notes the model gets with the user's next prompt.
type State = { isInteractive: boolean; forms: Record<Command, Form>; opens: number; notes: string[] }
// The fields' keys, new at each open: Claude Code keeps what was typed in a field, by its key, until the hook
// draws a different value, and an emptied form draws the same empty value, so an old key would show the old text.
const field = (state: State, name: 'name' | 'what') => `${name}-${state.opens}`
const empty = (): Form => ({ name: '', what: '', error: '' })

// The skill's template made into the new file: its name and description set, and its links, relative to the
// skill's folder and so dead in the copy, turned into the skill's command and the file's path in the skill.
function fill(source: string, command: Command, name: string, description: string) {
  const { noun, when, skill } = KINDS[command]
  const text = description.trim() || `TODO: what this ${noun} does, and ${when}.`
  return source
    .replace(/^name: .*$/m, () => `name: ${name}`)
    .replace(/^description: .*$/m, () => `description: >-\n  ${text}`)
    .replace(/\[([^\]]+)\]\(\.\/([^)]+)\)/g, (_, label: string, file: string) =>
      file === 'SKILL.md' ? `${label} (/${skill})` : `${label} (/${skill}, ${file})`)
}

// `Code_Reviewer` and `Code Reviewer` → `code-reviewer`.
const normalize = (name: string) => name.trim().toLowerCase().replace(/[\s_]+/g, '-')

// Writes the file, never over an existing one. `context` is the note for the model.
async function write($: EngineInterface, command: Command, name: string, description: string) {
  const { path, template, skill } = KINDS[command]
  if (!name) return { error: 'a name is needed' }
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(name)) return { error: `bad name '${name}': use lowercase letters, digits and hyphens` }
  const file = path(name)
  if (await $.fs.exists(file)) return { error: `already exists, left untouched: ${file}` }
  await $.fs.write(file, fill(await $.fs.read(`${$.plugin.root}/${template}`), command, name, description))
  return { text: `created ${file}`, context: `The user ran /${command}: it created ${file} from /${skill}'s template (name and description set, every other field commented out, the body a placeholder).` }
}

// `--name code-reviewer --description Reviews diffs` (or `--name=…`): each flag takes the words up to the next
// flag; quotes keep words together. Words before any flag are free text.
type Flag = 'name' | 'description'
function parse(args: string) {
  const words: Record<Flag | 'text', string[]> = { name: [], description: [], text: [] }
  let into: Flag | 'text' = 'text'
  let flagged = false
  for (const token of args.match(/"[^"]*"|'[^']*'|\S+/g) ?? []) {
    const flag = /^--(name|description)(?:=(.+))?$/.exec(token)
    if (flag) {
      into = flag[1] as Flag
      flagged = true
      if (flag[2]) words[into].push(flag[2])
      continue
    }
    words[into].push(token)
  }
  const join = (key: Flag | 'text') => words[key].map(w => w.replace(/^(["'])(.*)\1$/, '$2')).join(' ').trim()
  return { flagged, name: join('name'), description: join('description'), text: join('text') }
}

// `--name` (and `--description`): the file is written at once and the model gets a note. Free text: Claude is
// asked, the skill for designing one loaded. Nothing, in a session a user is at: the form opens in a pane.
async function run($: EngineInterface, state: State, command: Command, args: string) {
  const { flagged, name, description, text } = parse(args)
  if (flagged) {
    if (!name) return { text: `--name is needed: /${command} --name <name> --description <what it does>` }
    const result = await write($, command, normalize(name), description)
    return 'error' in result ? { text: result.error } : { text: result.text, context: [result.context] }
  }
  if (text) return ask($, command, text)
  if (!state.isInteractive) return { text: `usage: /${command} --name <name> --description <what it does>, or /${command} <what it should do>` }
  state.forms[command] = empty()
  state.opens += 1
  // rows: what the form needs inline, footer included (left out, a third of the screen, which cuts it).
  await $.ui.open({ id: command, title: KINDS[command].title, focus: true, closeOnEscape: true, rows: 12 })
  return {}
}

// Claude is asked to design it: the skill's own command, run with the words as if the user typed it, so the whole
// skill loads. Run after the hook returns: from inside a command.run hook, command.run would wait on the turn the
// hook holds, and Claude Code refuses it.
function ask($: EngineInterface, command: Command, words: string) {
  const { skill } = KINDS[command]
  $.clock.after(0, () => { void $.command.run({ command: skill, args: words }).catch(() => undefined) })
  return { text: `asking Claude, with /${skill}` }
}

async function create($: EngineInterface, state: State, command: Command) {
  const form = state.forms[command]
  const result = await write($, command, normalize(form.name), form.what)
  if ('error' in result) {
    form.error = result.error ?? ''
    $.ui.invalidate('ui.render')
    return
  }
  state.notes.push(result.context)
  await $.ui.close({ id: command })
  $.ui.log(result.text)
}

// Design with Claude: the pane closes and Claude is asked, with what the user typed.
async function design($: EngineInterface, state: State, command: Command) {
  const form = state.forms[command]
  const named = form.name.trim() ? `(name: ${normalize(form.name)})` : ''
  const words = [form.what.trim(), named].filter(Boolean).join(' ')
  await $.ui.close({ id: command })
  ask($, command, words || `a new ${KINDS[command].noun}`)
}

export const register: Register = on => {
  const state: State = { isInteractive: false, forms: { 'new-agent': empty(), 'new-skill': empty() }, opens: 0, notes: [] }

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    state.isInteractive = e.isInteractive
    // immediate: typed mid-turn, it runs at once instead of waiting for the turn to end
    await $.command.register({
      name: 'new-agent',
      description: 'Create .claude/agents/<name>.md, or design the agent with Claude',
      argumentHint: '[--name <name> --description <text> | what it should do]',
      immediate: true,
    })
    await $.command.register({
      name: 'new-skill',
      description: 'Create .claude/skills/<name>/SKILL.md, or design the skill with Claude',
      argumentHint: '[--name <name> --description <text> | what it should do]',
      immediate: true,
    })
    return started
  })

  // The hook IS the command: no next(e), so no model turn happens for it.
  on('command.run', { command: 'new-agent' }, ($, e) => run($, state, 'new-agent', e.args))
  on('command.run', { command: 'new-skill' }, ($, e) => run($, state, 'new-skill', e.args))

  // The form, drawn as Claude Code draws its own dialogs: a bold title in the accent color, dim help, the keys in
  // a dim footer, and docked a rounded border. Theme keys, so it follows the user's theme.
  on('ui.render', { component: 'Pane' }, ($, e, next) => {
    const command = e.requestId
    if (!isCommand(command) || !drawsInput(e)) return next(e)
    const { Box, Text, Input, Button } = $.ui.resolve(e)
    const { noun, title, path, example, sample, skill } = KINDS[command]
    const form = state.forms[command]
    const primary = { variant: 'primary' } // on 2.1.292, not yet in the typed props
    // Docked (fullscreen), the pane is a grey column with no frame: our border marks the form. Inline (the classic
    // renderer, above the prompt), Claude Code frames the pane itself, so a border of ours would be a box in a box.
    const frame = e.props.placement === 'dock' ? { borderStyle: 'round', borderColor: 'claude', paddingX: 1 } : {}
    // Enter in either field creates the file: Enter empties the field it is pressed in, so it cannot move to
    // the next one; Tab does.
    return (
      <Box flexDirection="column">
        <Box flexDirection="column" {...frame}>
          <Text bold color="claude">✻ {title}</Text>
          <Text color="subtle">Writes {path('<name>')} from the {noun} template.</Text>
          <Box marginTop={1} flexDirection="column">
            <Input key={field(state, 'name')} label="Name" placeholder={example} value={form.name} autoFocus submitLabel="create"
              onInput={value => { form.name = value }}
              onSubmit={value => { form.name = value; void create($, state, command) }} />
            <Input key={field(state, 'what')} label="Description" placeholder={sample} value={form.what} submitLabel="create"
              onInput={value => { form.what = value }}
              onSubmit={value => { form.what = value; void create($, state, command) }} />
          </Box>
          {form.error ? <Text color="error">✗ {form.error}</Text> : null}
          <Box marginTop={1} flexDirection="row" gap={2}>
            <Button key="create" {...primary} onPress={() => { void create($, state, command) }}>Create file</Button>
            <Button key="design" onPress={() => { void design($, state, command) }}>Design with Claude</Button>
          </Box>
          <Text color="subtle">Design with Claude asks Claude, with /{skill} loaded and your words.</Text>
        </Box>
        <Text color="subtle">{e.props.placement === 'dock' ? ' ' : ''}Tab next field · Enter create · Esc cancel</Text>
      </Box>
    )
  })

  on('ui.close', async ($, e, next) => {
    const closed = await next(e)
    if (isCommand(e.id)) state.forms[e.id] = empty()
    return closed
  })

  on('prompt.submit', ($, e, next) => {
    if (!state.notes.length) return next(e)
    const context = [...(e.context ?? []), ...state.notes]
    state.notes = []
    return next({ ...e, context })
  })
}
