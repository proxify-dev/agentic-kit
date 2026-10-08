import type { EngineInterface, Register } from 'claude-code'

// What each command writes, relative to the session's working directory (in the person's own plugin, the same path
// without `.claude/`, under its folder); the template it starts from, in this plugin; and the skill that designs one
// with Claude.
const KINDS = {
  'new-agent': { noun: 'agent', title: 'New agent', path: (name: string) => `.claude/agents/${name}.md`, when: 'when Claude should hand work to it', template: 'skills/agent-development/_template.md', skill: 'agentic-engineering:agent-development', example: 'code-reviewer', sample: 'Reviews diffs for bugs, after each change' },
  'new-skill': { noun: 'skill', title: 'New skill', path: (name: string) => `.claude/skills/${name}/SKILL.md`, when: 'when Claude should use it', template: 'skills/skill-development/_template.md', skill: 'agentic-engineering:skill-development', example: 'release-notes', sample: 'Writes the notes for a version' },
} as const
type Command = keyof typeof KINDS
const isCommand = (id: string): id is Command => id in KINDS
// Every surface but mobile draws Input, which the form needs.
const drawsInput = <E extends { surface: string }>(e: E): e is E & { surface: 'terminal' | 'desktop' | 'vscode' } => e.surface !== 'mobile'

// The person's own plugin, made by /new-plugin: its name and its folder (absolute). Kept in the plugin's store, so
// every session knows it.
type Home = { name: string; folder: string }
// Where /new-agent and /new-skill write: into that plugin, or under the session's working directory.
type Where = 'plugin' | 'project'
// What a form holds while its pane is open.
type Form = { name: string; what: string; where: Where; error: string }
type PluginForm = { name: string; folder: string; error: string; status: string }
// The module's state: whether a user is at the session, the session's working directory, the user's home folder,
// their plugin, each command's form, how many times a form has opened, and the notes the model gets with the
// user's next prompt.
type State = { isInteractive: boolean; cwd: string; userHome: string; home?: Home; forms: Record<Command, Form>; plugin: PluginForm; opens: number; notes: string[] }
// The fields' keys, new at each open: Claude Code keeps what was typed in a field, by its key, until the hook
// draws a different value, and an emptied form draws the same empty value, so an old key would show the old text.
const field = (state: State, name: 'name' | 'what' | 'where' | 'folder') => `${name}-${state.opens}`
const empty = (where: Where = 'project'): Form => ({ name: '', what: '', where, error: '' })
const emptyPlugin = (): PluginForm => ({ name: '', folder: '', error: '', status: '' })
const NAME = /^[a-z0-9][a-z0-9-]{0,63}$/

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
// `/Users/ana/ana-claude` → `~/ana-claude`, in what the user reads.
const tilde = (state: State, path: string) =>
  state.userHome && path.startsWith(`${state.userHome}/`) ? `~${path.slice(state.userHome.length)}` : path
// What the user typed as a folder, made absolute: `~/x` under their home folder, a relative path under the
// session's working directory.
function expand(state: State, typed: string) {
  const path = typed.length > 1 ? typed.replace(/[\\/]+$/, '') : typed
  if (path === '~' || path.startsWith('~/')) return `${state.userHome}${path.slice(1)}`
  return /^([a-zA-Z]:)?[\\/]/.test(path) ? path : `${state.cwd}/${path}`
}

// The file a command writes: under the working directory, or in the plugin.
function target(command: Command, name: string, home?: Home) {
  const file = KINDS[command].path(name)
  return home ? `${home.folder}/${file.slice('.claude/'.length)}` : file
}

// The user's plugin, when /new-plugin made one and its folder still holds it.
async function loadHome($: EngineInterface): Promise<Home | undefined> {
  try {
    const home = (await $.store.get('home')) as Home | undefined
    return home && (await $.fs.exists(`${home.folder}/.claude-plugin/plugin.json`)) ? home : undefined
  } catch {
    return undefined
  }
}

// Plugins reload once the command has answered: from inside a command.run hook, command.run would wait on the turn
// the hook holds. A reload restarts only the modules that changed, so this one keeps its state.
function reload($: EngineInterface) {
  $.clock.after(0, () => {
    void $.command.run({ command: 'reload-plugins' }).catch(() => $.ui.log('run /reload-plugins to load it now'))
  })
}

// Writes the file, never over an existing one. `context` is the note for the model.
async function write($: EngineInterface, state: State, command: Command, name: string, description: string, where: Where) {
  const { template, skill } = KINDS[command]
  if (!name) return { error: 'a name is needed' }
  if (!NAME.test(name)) return { error: `bad name '${name}': use lowercase letters, digits and hyphens` }
  const home = where === 'plugin' ? state.home : undefined
  const file = target(command, name, home)
  if (await $.fs.exists(file)) return { error: `already exists, left untouched: ${tilde(state, file)}` }
  await $.fs.write(file, fill(await $.fs.read(`${$.plugin.root}/${template}`), command, name, description))
  const created = `The user ran /${command}: it created ${file} from /${skill}'s template (name and description set, every other field commented out, the body a placeholder).`
  if (!home) return { text: `created ${file}`, context: created }
  reload($)
  return {
    text: `created ${tilde(state, file)}, in your plugin: ${home.name}:${name}`,
    context: `${created} It is in the user's own plugin, ${home.name} (${home.folder}), so Claude Code knows it as ${home.name}:${name}; the command reloaded the plugins.`,
  }
}

// /new-plugin: the user's own plugin, in a folder they pick. The folder is also a one-plugin marketplace, so Claude
// Code reads the plugin from it (an edit there loads at /reload-plugins), installed for every session, and kept as
// where /new-agent and /new-skill write. A folder that already holds this plugin (a clone, a run that stopped
// half-way) is added and installed, nothing in it written over.
async function createPlugin($: EngineInterface, state: State, typed: string, typedFolder: string) {
  const name = normalize(typed)
  if (!name) return { error: 'a name is needed' }
  if (!NAME.test(name)) return { error: `bad name '${name}': use lowercase letters, digits and hyphens` }
  const folder = expand(state, typedFolder.trim() || `~/${name}-claude`)
  const shown = tilde(state, folder)
  const manifest = `${folder}/.claude-plugin/plugin.json`
  const isAdopted = await $.fs.exists(manifest)
  if (isAdopted) {
    let found: string | undefined
    try {
      found = (JSON.parse(await $.fs.read(manifest)) as { name?: string }).name
    } catch {
      return { error: `cannot read ${tilde(state, manifest)}: fix it, or pick another folder` }
    }
    if (found !== name) return { error: `${shown} holds the plugin '${found}': name it ${found}, or pick another folder` }
  } else if ((await $.fs.exists(folder)) && (await $.fs.list(folder)).length) {
    return { error: `not empty, left untouched: ${shown}` }
  }
  const settings = await $.settings.read()
  const marketplaces = (settings.extraKnownMarketplaces ?? {}) as Record<string, { source?: { path?: string } }>
  const enabled = Object.keys((settings.enabledPlugins ?? {}) as Record<string, unknown>)
  const known = marketplaces[name]
  if (known && known.source?.path !== folder) return { error: `a marketplace named ${name} already comes from elsewhere: pick another name` }
  const other = enabled.find(id => id.startsWith(`${name}@`) && id !== `${name}@${name}`)
  if (other) return { error: `a plugin named ${name} is already installed (${other}): pick another name` }

  const files = await scaffold($, name)
  for (const [path, text] of Object.entries(files)) {
    if (isAdopted && (path !== '.claude-plugin/marketplace.json' || (await $.fs.exists(`${folder}/${path}`)))) continue
    await $.fs.write(`${folder}/${path}`, text)
  }
  if (!isAdopted) await $.process.run(['git', 'init', '-q'], { cwd: folder }).catch(() => undefined)
  const steps = [
    ...(known ? [] : [['claude', 'plugin', 'marketplace', 'add', folder]]),
    ...(enabled.includes(`${name}@${name}`) ? [] : [['claude', 'plugin', 'install', `${name}@${name}`, '--scope', 'user']]),
  ]
  for (const argv of steps) {
    const { exitCode, stdout, stderr } = await $.process.run(argv, { timeoutMs: 120_000 })
    const said = (stderr.trim() || stdout.trim()).split('\n').pop() ?? ''
    if (exitCode !== 0) return { error: `${argv.slice(0, 4).join(' ')} failed: ${said}` }
  }
  state.home = { name, folder }
  await $.store.set('home', state.home)
  reload($)
  return {
    text: `${isAdopted ? 'added' : 'created'} your plugin ${name} at ${shown}: /new-skill and /new-agent write there now`,
    context: `The user ran /new-plugin: their own plugin, ${name}, is at ${folder} (a git repo: .claude-plugin/plugin.json and marketplace.json, skills/, agents/, README.md, AGENTS.md), added as the marketplace ${name} and installed as ${name}@${name} for every session. Claude Code reads it from that folder: an edit there loads at /reload-plugins, which the command ran. /new-skill and /new-agent now write into it unless the user picks this project.`,
  }
}

// The files of a new plugin, by path in its folder: the manifest, the folder as a one-plugin marketplace, the
// README and AGENTS.md from this plugin's templates, a CLAUDE.md that imports AGENTS.md (Claude Code reads
// CLAUDE.md, not AGENTS.md), and the two folders the commands write into.
async function scaffold($: EngineInterface, name: string): Promise<Record<string, string>> {
  const description = `${name}'s own skills and agents, in every session.`
  const json = (value: unknown) => `${JSON.stringify(value, null, 2)}\n`
  const template = async (file: string) => (await $.fs.read(`${$.plugin.root}/templates/${file}`)).replaceAll('{{name}}', name)
  return {
    '.claude-plugin/plugin.json': json({ name, version: '0.1.0', description }),
    '.claude-plugin/marketplace.json': json({ name, owner: { name }, plugins: [{ name, source: './', description }] }),
    'README.md': await template('plugin-README.md'),
    'AGENTS.md': await template('plugin-AGENTS.md'),
    'CLAUDE.md': '@AGENTS.md\n',
    '.gitignore': '.DS_Store\n',
    'skills/.gitkeep': '',
    'agents/.gitkeep': '',
  }
}

// `--name code-reviewer --description Reviews diffs` (or `--name=…`): each flag takes the words up to the next
// flag; quotes keep words together. Words before any flag are free text.
type Flag = 'name' | 'description' | 'where' | 'folder'
function parse(args: string) {
  const words: Record<Flag | 'text', string[]> = { name: [], description: [], where: [], folder: [], text: [] }
  let into: Flag | 'text' = 'text'
  let flagged = false
  for (const token of args.match(/"[^"]*"|'[^']*'|\S+/g) ?? []) {
    const flag = /^--(name|description|where|folder)(?:=(.+))?$/.exec(token)
    if (flag) {
      into = flag[1] as Flag
      flagged = true
      if (flag[2]) words[into].push(flag[2])
      continue
    }
    words[into].push(token)
  }
  const join = (key: Flag | 'text') => words[key].map(w => w.replace(/^(["'])(.*)\1$/, '$2')).join(' ').trim()
  return { flagged, name: join('name'), description: join('description'), where: join('where'), folder: join('folder'), text: join('text') }
}

// `--name` (and `--description`): the file is written at once and the model gets a note. Free text: Claude is
// asked, the skill for designing one loaded. Nothing, in a session a user is at: the form opens in a pane.
// `--where plugin|project` picks where; without it, the user's plugin when they have one.
async function run($: EngineInterface, state: State, command: Command, args: string) {
  state.home = await loadHome($)
  const { flagged, name, description, where, text } = parse(args)
  const place = where || (state.home ? 'plugin' : 'project')
  if (place !== 'plugin' && place !== 'project') return { text: `--where takes plugin or project, not '${place}'` }
  if (place === 'plugin' && !state.home) return { text: 'no plugin of yours yet: /new-plugin makes one' }
  if (flagged) {
    if (!name) return { text: `--name is needed: /${command} --name <name> --description <what it does>` }
    const result = await write($, state, command, normalize(name), description, place)
    return 'error' in result ? { text: result.error } : { text: result.text, context: [result.context] }
  }
  if (text) return ask($, state, command, text, place)
  if (!state.isInteractive) return { text: `usage: /${command} --name <name> --description <what it does>, or /${command} <what it should do>` }
  state.forms[command] = empty(place)
  state.opens += 1
  // rows: what the form needs inline, footer included (left out, a third of the screen, which cuts it).
  await $.ui.open({ id: command, title: KINDS[command].title, focus: true, closeOnEscape: true, rows: state.home ? 13 : 12 })
  return {}
}

// `/new-plugin my-tools` or `--name my-tools --folder ~/code/my-tools`: the plugin is made at once. Nothing, in a session a
// user is at: the form opens in a pane.
async function runPlugin($: EngineInterface, state: State, args: string) {
  const { name, folder, text } = parse(args)
  const typed = name || text
  if (typed) {
    const result = await createPlugin($, state, typed, folder)
    return 'error' in result ? { text: result.error } : { text: result.text, context: [result.context] }
  }
  if (!state.isInteractive) return { text: 'usage: /new-plugin <name>, or /new-plugin --name <name> --folder <folder>' }
  state.plugin = emptyPlugin()
  state.opens += 1
  await $.ui.open({ id: 'new-plugin', title: 'New plugin', focus: true, closeOnEscape: true, rows: 12 })
  return {}
}

// Claude is asked to design it: the skill's own command, run with the words as if the user typed it, so the whole
// skill loads. Run after the hook returns: from inside a command.run hook, command.run would wait on the turn the
// hook holds, and Claude Code refuses it. Into the plugin, the words say where the file goes.
function ask($: EngineInterface, state: State, command: Command, words: string, where: Where) {
  const { skill } = KINDS[command]
  const home = where === 'plugin' ? state.home : undefined
  const into = home ? ` (put it in my plugin ${home.name}: ${target(command, '<name>', home)})` : ''
  $.clock.after(0, () => { void $.command.run({ command: skill, args: `${words}${into}` }).catch(() => undefined) })
  return { text: `asking Claude, with /${skill}` }
}

async function create($: EngineInterface, state: State, command: Command) {
  const form = state.forms[command]
  const result = await write($, state, command, normalize(form.name), form.what, form.where)
  if ('error' in result) {
    form.error = result.error ?? ''
    $.ui.invalidate('ui.render')
    return
  }
  state.notes.push(result.context)
  await $.ui.close({ id: command })
  $.ui.log(result.text)
}

async function createFromForm($: EngineInterface, state: State) {
  const form = state.plugin
  form.error = ''
  form.status = 'creating…'
  $.ui.invalidate('ui.render')
  const result = await createPlugin($, state, form.name, form.folder)
  form.status = ''
  if ('error' in result) {
    form.error = result.error ?? ''
    $.ui.invalidate('ui.render')
    return
  }
  state.notes.push(result.context)
  await $.ui.close({ id: 'new-plugin' })
  $.ui.log(result.text)
}

// Design with Claude: the pane closes and Claude is asked, with what the user typed.
async function design($: EngineInterface, state: State, command: Command) {
  const form = state.forms[command]
  const named = form.name.trim() ? `(name: ${normalize(form.name)})` : ''
  const words = [form.what.trim(), named].filter(Boolean).join(' ')
  await $.ui.close({ id: command })
  ask($, state, command, words || `a new ${KINDS[command].noun}`, form.where)
}

export const register: Register = on => {
  const state: State = { isInteractive: false, cwd: '', userHome: '', forms: { 'new-agent': empty(), 'new-skill': empty() }, plugin: emptyPlugin(), opens: 0, notes: [] }

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    state.isInteractive = e.isInteractive
    state.cwd = e.cwd
    state.userHome = ((await $.env.get('HOME').catch(() => undefined)) ?? (await $.env.get('USERPROFILE').catch(() => undefined)) ?? '').replace(/[\\/]$/, '')
    // immediate: typed mid-turn, it runs at once instead of waiting for the turn to end
    await $.command.register({
      name: 'new-agent',
      description: 'Create an agent file, in your plugin or this project, or design the agent with Claude',
      argumentHint: '[--name <name> --description <text> --where plugin|project | what it should do]',
      immediate: true,
    })
    await $.command.register({
      name: 'new-skill',
      description: 'Create a SKILL.md, in your plugin or this project, or design the skill with Claude',
      argumentHint: '[--name <name> --description <text> --where plugin|project | what it should do]',
      immediate: true,
    })
    await $.command.register({
      name: 'new-plugin',
      description: 'Make your own plugin in a folder you pick: /new-skill and /new-agent then write into it',
      argumentHint: '[<name> | --name <name> --folder <folder>]',
      immediate: true,
    })
    return started
  })

  // The hook IS the command: no next(e), so no model turn happens for it.
  on('command.run', { command: 'new-agent' }, ($, e) => run($, state, 'new-agent', e.args))
  on('command.run', { command: 'new-skill' }, ($, e) => run($, state, 'new-skill', e.args))
  on('command.run', { command: 'new-plugin' }, ($, e) => runPlugin($, state, e.args))

  // The forms, drawn as Claude Code draws its own dialogs: a bold title in the accent color, dim help, the keys in
  // a dim footer, and docked a rounded border. Theme keys, so it follows the user's theme.
  on('ui.render', { component: 'Pane' }, ($, e, next) => {
    const id = e.requestId
    if (!(isCommand(id) || id === 'new-plugin') || !drawsInput(e)) return next(e)
    const { Box, Text, Input, Button, Select } = $.ui.resolve(e)
    const primary = { variant: 'primary' } // on 2.1.292, not yet in the typed props
    // Docked (fullscreen), the pane is a grey column with no frame: our border marks the form. Inline (the classic
    // renderer, above the prompt), Claude Code frames the pane itself, so a border of ours would be a box in a box.
    const frame = e.props.placement === 'dock' ? { borderStyle: 'round', borderColor: 'claude', paddingX: 1 } : {}
    const footer = <Text color="subtle">{e.props.placement === 'dock' ? ' ' : ''}Tab next field · Enter create · Esc cancel</Text>

    if (id === 'new-plugin') {
      const form = state.plugin
      const name = normalize(form.name) || '<name>'
      return (
        <Box flexDirection="column">
          <Box flexDirection="column" {...frame}>
            <Text bold color="claude">✻ New plugin</Text>
            <Text color="subtle">Your own skills and agents, in a folder you pick: a git repo, on in every session.</Text>
            <Box marginTop={1} flexDirection="column">
              <Input key={field(state, 'name')} label="Name" placeholder="your-name" value={form.name} autoFocus submitLabel="create"
                onInput={value => { form.name = value; $.ui.invalidate('ui.render') }}
                onSubmit={value => { form.name = value; void createFromForm($, state) }} />
              <Input key={field(state, 'folder')} label="Folder" placeholder={`~/${name}-claude`} value={form.folder} submitLabel="create"
                onInput={value => { form.folder = value }}
                onSubmit={value => { form.folder = value; void createFromForm($, state) }} />
            </Box>
            <Text color="subtle">Its skills run as /{name}:&lt;skill&gt;: a short name reads best.</Text>
            {form.error ? <Text color="error">✗ {form.error}</Text> : null}
            {form.status ? <Text color="subtle">{form.status}</Text> : null}
            <Box marginTop={1} flexDirection="row" gap={2}>
              <Button key="create" {...primary} onPress={() => { void createFromForm($, state) }}>Create plugin</Button>
            </Box>
          </Box>
          {footer}
        </Box>
      )
    }

    const { noun, title, example, sample, skill } = KINDS[id]
    const form = state.forms[id]
    const home = state.home
    const file = target(id, '<name>', form.where === 'plugin' ? home : undefined)
    // Enter in either field creates the file: Enter empties the field it is pressed in, so it cannot move to
    // the next one; Tab does.
    return (
      <Box flexDirection="column">
        <Box flexDirection="column" {...frame}>
          <Text bold color="claude">✻ {title}</Text>
          <Text color="subtle">Writes {tilde(state, file)} from the {noun} template.</Text>
          <Box marginTop={1} flexDirection="column">
            {home ? (
              <Select key={field(state, 'where')} label="Where" value={form.where}
                options={[{ value: 'plugin', label: `${home.name} (${tilde(state, home.folder)})` }, { value: 'project', label: 'this project (.claude/)' }]}
                onSelect={value => { form.where = value === 'project' ? 'project' : 'plugin'; $.ui.invalidate('ui.render') }} />
            ) : null}
            <Input key={field(state, 'name')} label="Name" placeholder={example} value={form.name} autoFocus submitLabel="create"
              onInput={value => { form.name = value }}
              onSubmit={value => { form.name = value; void create($, state, id) }} />
            <Input key={field(state, 'what')} label="Description" placeholder={sample} value={form.what} submitLabel="create"
              onInput={value => { form.what = value }}
              onSubmit={value => { form.what = value; void create($, state, id) }} />
          </Box>
          {form.error ? <Text color="error">✗ {form.error}</Text> : null}
          <Box marginTop={1} flexDirection="row" gap={2}>
            <Button key="create" {...primary} onPress={() => { void create($, state, id) }}>Create file</Button>
            <Button key="design" onPress={() => { void design($, state, id) }}>Design with Claude</Button>
          </Box>
          <Text color="subtle">Design with Claude asks Claude, with /{skill} loaded and your words.</Text>
        </Box>
        {footer}
      </Box>
    )
  })

  on('ui.close', async ($, e, next) => {
    const closed = await next(e)
    if (isCommand(e.id)) state.forms[e.id] = empty()
    if (e.id === 'new-plugin') state.plugin = emptyPlugin()
    return closed
  })

  on('prompt.submit', ($, e, next) => {
    if (!state.notes.length) return next(e)
    const context = [...(e.context ?? []), ...state.notes]
    state.notes = []
    return next({ ...e, context })
  })
}
