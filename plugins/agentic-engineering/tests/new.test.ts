import type { On } from 'claude-code'
import { describe, expect, mock, test, tier } from 'claude-code/testing'

tier('user')

const run = (command: string, args: string) => ({
  command,
  args,
  origin: { kind: 'composer' as const },
  presentation: { isFullscreen: false, columns: 80 },
})

// The skills' templates, cut to the lines the commands change or keep.
const TEMPLATES = {
  'skills/agent-development/_template.md': '---\n# Only name and description are required.\nname: agent-name\n# The requests first: [how Claude chooses an agent](./SKILL.md)\ndescription: When Claude should delegate to this agent.\n# model: inherit\n---\n\n<!-- Replace this comment: [writing the body](./body.md) -->\n',
  'skills/skill-development/_template.md': '---\nname: skill-name\ndescription: What this skill does and when to use it.\n---\n',
  'templates/plugin-README.md': '# {{name}}\n\n| skill | `skills/<skill>/SKILL.md` | `/{{name}}:<skill>` |\n',
  'templates/plugin-AGENTS.md': '# {{name}}\n\n- Use skill /plugin-authoring and /agentic-engineering:harness-engineering for advanced customization.\n',
}

// No disk in a test: these hooks stand in for it, holding what was written and answering the templates. The
// engine hands them the path made absolute under the working directory; they key it from its last `.claude/` on,
// since the working directory can hold one itself (a worktree under .claude/worktrees/). A path in the user's
// plugin, under their home folder, is kept whole. A folder exists when a file is under it.
const disk = (on: On, present: string[] = [], texts: Record<string, string> = {}) => {
  const files = new Map<string, string>([...present.map((p): [string, string] => [p, '']), ...Object.entries(texts)])
  const rel = (path: string) => path.startsWith(HOME) ? path : path.slice(path.lastIndexOf('.claude/'))
  const under = (dir: string) => [...files.keys()].filter(path => path.startsWith(`${rel(dir)}/`))
  on('fs.read', ($, e, next) => {
    const template = Object.entries(TEMPLATES).find(([path]) => e.path.endsWith(path))
    if (template) return { value: template[1] }
    const text = files.get(rel(e.path))
    return text === undefined ? next(e) : { value: text }
  })
  on('fs.exists', ($, e) => ({ value: files.has(rel(e.path)) || under(e.path).length > 0 }))
  on('fs.list', ($, e) => ({ value: under(e.path).map(path => ({ name: path.slice(rel(e.path).length + 1), kind: 'file' as const, size: 0, isLink: false })) }))
  on('fs.write', ($, e) => {
    files.set(rel(e.path), e.text)
    return { value: undefined }
  })
  return files
}

// The rest of the machine /new-plugin touches: the user's home folder, this plugin's store, the user's settings, and
// the commands run on the host, each exiting 0 unless it starts with `fails`.
const HOME = '/home/ana'
const PLUGIN = `${HOME}/ana-claude`
const machine = (on: On, { store = {}, settings = {}, fails = '' }: { store?: Record<string, unknown>; settings?: Record<string, unknown>; fails?: string } = {}) => {
  const kept = new Map(Object.entries(store))
  const ran: string[] = []
  on('env.get', ($, e) => ({ value: e.name === 'HOME' ? HOME : undefined }))
  on('store.get', ($, e) => ({ value: kept.get(e.key) }))
  on('store.set', ($, e) => {
    kept.set(e.key, e.value)
    return { value: undefined }
  })
  on('settings.read', () => ({ value: settings }))
  on('process.run', ($, e) => {
    const line = [...e.argv, ...(e.init?.cwd ? [`(in ${e.init.cwd})`] : [])].join(' ')
    ran.push(line)
    const failed = fails !== '' && line.startsWith(fails)
    return { value: failed ? { exitCode: 1, stdout: '', stderr: 'Error: it broke\nno such marketplace\n' } : { exitCode: 0, stdout: '', stderr: '' } }
  })
  return { kept, ran }
}
// A user whose plugin /new-plugin made: in the store, its manifest in its folder.
const HAS_PLUGIN = { store: { home: { name: 'ana', folder: PLUGIN } } }
const MANIFEST = `${PLUGIN}/.claude-plugin/plugin.json`

const SKILLS = ['agentic-engineering:agent-development', 'agentic-engineering:skill-development', 'reload-plugins']

// A session, its clock the test's: the panes opened (and how), closed, the lines logged, the skill commands run
// (`/name args`), and each submitted prompt with the context it carried.
const session = (on: On) => {
  const seen = {
    opened: [] as { id: string; rows?: number; focus?: true; closeOnEscape?: true }[],
    closed: [] as string[],
    logged: [] as string[],
    ran: [] as string[],
    submitted: [] as { text: string; context?: readonly string[] }[],
  }
  const clock = mock.clock(on)
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  for (const skill of SKILLS) {
    on('command.run', { command: skill }, ($, e) => {
      seen.ran.push(`/${e.command} ${e.args}`)
      return {}
    })
  }
  on('ui.open', ($, e) => {
    seen.opened.push({ id: e.id, rows: e.rows, focus: e.focus, closeOnEscape: e.closeOnEscape })
    return { value: undefined }
  })
  on('ui.close', ($, e) => {
    seen.closed.push(e.id)
    return { value: undefined }
  })
  on('ui.invalidate', () => ({ value: undefined }))
  on('ui.log', ($, e) => {
    seen.logged.push(e.text)
    return { value: undefined }
  })
  on('prompt.submit', ($, e) => {
    seen.submitted.push({ text: e.text, context: e.context })
    return { text: e.text, context: e.context }
  })
  return { seen, clock }
}

const start = (isInteractive = true) => ({ surface: 'terminal' as const, isInteractive, cwd: '/work' })
const submit = (text: string) => ({ text, wait: false, origin: { kind: 'composer' as const } })

// The pane as the terminal seats it: docked beside a fullscreen transcript, or inline above the prompt.
const pane = (requestId: string, placement: 'dock' | 'inline' = 'dock') => ({
  plugin: 'agentic-engineering',
  surface: 'terminal' as const,
  component: 'Pane' as const,
  requestId,
  props: { title: '', isFocused: true, bodyColumns: 80, placement, scroll: { offset: 0, bodyRows: 12 }, view: {} },
})

describe('new', () => {
  test('registers /new-agent, /new-skill and /new-plugin as immediate commands', async ($, on) => {
    const registered: { name: string; immediate?: true }[] = []
    on('session.start', ($, e) => ({ cwd: e.cwd }))
    on('command.register', ($, e) => {
      registered.push({ name: e.name, immediate: e.immediate })
      return { value: { command: e.name } }
    })
    await $.session.start(start())
    expect(registered).toEqual([
      { name: 'new-agent', immediate: true },
      { name: 'new-skill', immediate: true },
      { name: 'new-plugin', immediate: true },
    ])
  })

  describe('--name and --description: the file is written at once, and the model is told', () => {
    test('/new-agent --name writes the agent from the template, its description a TODO, and tells the model', async ($, on) => {
      const files = disk(on)
      const { text, context } = await $.command.run(run('new-agent', '--name code-reviewer'))
      expect(text).toBe('created .claude/agents/code-reviewer.md')
      expect(files.get('.claude/agents/code-reviewer.md')).toContain('name: code-reviewer\n')
      expect(files.get('.claude/agents/code-reviewer.md')).toContain('description: >-\n  TODO: what this agent does, and when Claude should hand work to it.\n')
      expect(context).toEqual(["The user ran /new-agent: it created .claude/agents/code-reviewer.md from /agentic-engineering:agent-development's template (name and description set, every other field commented out, the body a placeholder)."])
    })

    test("the template's other lines stay, and its links, dead in the copy, name the skill and the file", async ($, on) => {
      const files = disk(on)
      await $.command.run(run('new-agent', '--name code-reviewer --description Reviews diffs'))
      const written = files.get('.claude/agents/code-reviewer.md')
      expect(written).toContain('# model: inherit\n')
      expect(written).toContain('# The requests first: how Claude chooses an agent (/agentic-engineering:agent-development)\n')
      expect(written).toContain('<!-- Replace this comment: writing the body (/agentic-engineering:agent-development, body.md) -->')
    })

    test('/new-skill writes SKILL.md in its own folder; --description takes the words up to the next flag, quoted or not', async ($, on) => {
      const files = disk(on)
      await $.command.run(run('new-skill', '--description "Writes notes: per version" --name Release_Notes'))
      expect(files.get('.claude/skills/release-notes/SKILL.md')).toContain('description: >-\n  Writes notes: per version\n')
      await $.command.run(run('new-agent', '--name=doc-writer --description Writes the docs'))
      expect(files.get('.claude/agents/doc-writer.md')).toContain('description: >-\n  Writes the docs\n')
    })

    test('an existing file is left untouched', async ($, on) => {
      const files = disk(on, ['.claude/agents/taken.md'])
      const { text, context } = await $.command.run(run('new-agent', '--name taken --description x'))
      expect(text).toBe('already exists, left untouched: .claude/agents/taken.md')
      expect(files.get('.claude/agents/taken.md')).toBe('')
      expect(context).toBe(undefined)
    })

    test('--description without --name, or a bad name, writes nothing', async ($, on) => {
      const files = disk(on)
      expect((await $.command.run(run('new-agent', '--description Reviews diffs'))).text).toBe('--name is needed: /new-agent --name <name> --description <what it does>')
      expect((await $.command.run(run('new-agent', '--name foo/bar'))).text).toBe("bad name 'foo/bar': use lowercase letters, digits and hyphens")
      expect(files.size).toBe(0)
    })

    test('in a session a user is at, the flags open no pane', async ($, on) => {
      const { seen } = session(on)
      disk(on)
      await $.session.start(start())
      await $.command.run(run('new-agent', '--name code-reviewer --description Reviews diffs'))
      expect(seen.opened).toEqual([])
    })
  })

  describe('free text: Claude is asked, after the command answers', () => {
    test('the skill command runs with the words, so the whole skill loads; nothing is written', async ($, on) => {
      const { seen, clock } = session(on)
      const files = disk(on)
      await $.session.start(start())
      const { text } = await $.command.run(run('new-agent', 'a reviewer for pull requests'))
      expect(text).toBe('asking Claude, with /agentic-engineering:agent-development')
      expect(seen.ran).toEqual([])
      await clock.settle()
      expect(seen.ran).toEqual(['/agentic-engineering:agent-development a reviewer for pull requests'])
      expect(files.size).toBe(0)
    })

    test('headless, nothing after the command shows the usage', async ($, on) => {
      session(on)
      await $.session.start(start(false))
      expect((await $.command.run(run('new-agent', ''))).text).toBe('usage: /new-agent --name <name> --description <what it does>, or /new-agent <what it should do>')
    })
  })

  describe('nothing after the command, a user at the session: the form in a pane', () => {
    test('opens the pane focused, closing on Escape, tall enough for the form inline', async ($, on) => {
      const { seen } = session(on)
      await $.session.start(start())
      await $.command.run(run('new-agent', ''))
      expect(seen.opened).toEqual([{ id: 'new-agent', rows: 12, focus: true, closeOnEscape: true }])
    })

    test('Create file writes it, closes the pane, and the model gets the note with the next prompt only', async ($, on) => {
      const { seen } = session(on)
      const files = disk(on)
      await $.session.start(start())
      await $.command.run(run('new-agent', ''))
      const form = await $.ui.mount(pane('new-agent'))
      await form.input({ key: 'name-1', text: 'Code Reviewer', kind: 'change' })
      await form.input({ key: 'what-1', text: 'Reviews diffs', kind: 'change' })
      await form.press({ key: 'create' })
      expect(files.get('.claude/agents/code-reviewer.md')).toContain('description: >-\n  Reviews diffs\n')
      expect(seen.closed).toEqual(['new-agent'])
      expect(seen.logged).toEqual(['created .claude/agents/code-reviewer.md'])
      await $.prompt.submit(submit('fill in its body'))
      await $.prompt.submit(submit('and another'))
      expect(seen.submitted.map(s => s.context)).toEqual([
        ["The user ran /new-agent: it created .claude/agents/code-reviewer.md from /agentic-engineering:agent-development's template (name and description set, every other field commented out, the body a placeholder)."],
        undefined,
      ])
    })

    test('Enter in a field creates the file too', async ($, on) => {
      session(on)
      const files = disk(on)
      await $.session.start(start())
      await $.command.run(run('new-skill', ''))
      const form = await $.ui.mount(pane('new-skill'))
      await form.input({ key: 'name-1', text: 'release-notes' })
      expect(files.has('.claude/skills/release-notes/SKILL.md')).toBe(true)
    })

    test('a bad name shows the error in the pane, which stays open, and writes nothing', async ($, on) => {
      const { seen } = session(on)
      const files = disk(on)
      await $.session.start(start())
      await $.command.run(run('new-agent', ''))
      const form = await $.ui.mount(pane('new-agent'))
      await form.input({ key: 'name-1', text: 'foo/bar', kind: 'change' })
      await form.press({ key: 'create' })
      await form.redraw() // the test's ui.invalidate hook answers in place of the engine, so it draws nothing again
      expect(await form.find({ text: "bad name 'foo/bar'" })).toBeDefined()
      expect(files.size).toBe(0)
      expect(seen.closed).toEqual([])
    })

    test('Design with Claude closes the pane and runs the skill command with the words', async ($, on) => {
      const { seen, clock } = session(on)
      const files = disk(on)
      await $.session.start(start())
      await $.command.run(run('new-agent', ''))
      const form = await $.ui.mount(pane('new-agent'))
      await form.input({ key: 'name-1', text: 'code-reviewer', kind: 'change' })
      await form.input({ key: 'what-1', text: 'Reviews diffs', kind: 'change' })
      await form.press({ key: 'design' })
      await clock.settle()
      expect(seen.closed).toEqual(['new-agent'])
      expect(seen.ran).toEqual(['/agentic-engineering:agent-development Reviews diffs (name: code-reviewer)'])
      expect(files.size).toBe(0)
    })

    test('each open gets new field keys, so text typed in an earlier open does not show', async ($, on) => {
      session(on)
      await $.session.start(start())
      await $.command.run(run('new-agent', ''))
      await $.command.run(run('new-agent', ''))
      const form = await $.ui.mount(pane('new-agent'))
      expect(await form.find({ key: 'name-2' })).toBeDefined()
      expect(await form.find({ key: 'name-1' })).toBe(undefined)
    })

    test('docked, the form has its own rounded border; inline, Claude Code frames the pane and it has none', async ($, on) => {
      session(on)
      await $.session.start(start())
      await $.command.run(run('new-agent', ''))
      const bordered = async (placement: 'dock' | 'inline') => {
        const form = await $.ui.mount(pane('new-agent', placement))
        const boxes = await form.findAll({ type: 'Box' })
        await form.unmount()
        return boxes.some(b => b.props.borderStyle === 'round')
      }
      expect(await bordered('dock')).toBe(true)
      expect(await bordered('inline')).toBe(false)
    })
  })

  describe('/new-plugin: the user\'s own plugin, in a folder they pick', () => {
    test('writes the plugin as a one-plugin marketplace, makes it a git repo, adds and installs it, and keeps it as the home', async ($, on) => {
      const { seen, clock } = session(on)
      const files = disk(on)
      const { kept, ran } = machine(on)
      await $.session.start(start())
      const { text, context } = await $.command.run(run('new-plugin', 'Ana'))
      expect(text).toBe('created your plugin ana at ~/ana-claude: /new-skill and /new-agent write there now')
      expect(JSON.parse(files.get(MANIFEST) ?? '')).toEqual({ name: 'ana', version: '0.1.0', description: "ana's own skills and agents, in every session." })
      expect(JSON.parse(files.get(`${PLUGIN}/.claude-plugin/marketplace.json`) ?? '').plugins).toEqual([{ name: 'ana', source: './', description: "ana's own skills and agents, in every session." }])
      expect(files.get(`${PLUGIN}/README.md`)).toBe('# ana\n\n| skill | `skills/<skill>/SKILL.md` | `/ana:<skill>` |\n')
      expect(files.get(`${PLUGIN}/AGENTS.md`)).toBe('# ana\n\n- Use skill /plugin-authoring and /agentic-engineering:harness-engineering for advanced customization.\n')
      expect(files.get(`${PLUGIN}/CLAUDE.md`)).toBe('@AGENTS.md\n')
      expect([...files.keys()].filter(path => path.startsWith(PLUGIN)).sort()).toEqual([
        `${PLUGIN}/.claude-plugin/marketplace.json`, MANIFEST, `${PLUGIN}/.gitignore`, `${PLUGIN}/AGENTS.md`, `${PLUGIN}/CLAUDE.md`, `${PLUGIN}/README.md`, `${PLUGIN}/agents/.gitkeep`, `${PLUGIN}/skills/.gitkeep`,
      ])
      expect(ran).toEqual([`git init -q (in ${PLUGIN})`, `claude plugin marketplace add ${PLUGIN}`, 'claude plugin install ana@ana --scope user'])
      expect(kept.get('home')).toEqual({ name: 'ana', folder: PLUGIN })
      expect(context?.[0]).toContain(`their own plugin, ana, is at ${PLUGIN}`)
      expect(seen.ran).toEqual([])
      await clock.settle()
      expect(seen.ran).toEqual(['/reload-plugins '])
    })

    test('--folder takes ~/ and a path relative to the working directory', async ($, on) => {
      session(on)
      disk(on)
      const { kept } = machine(on)
      await $.session.start(start())
      await $.command.run(run('new-plugin', '--name ana --folder ~/code/mine'))
      expect(kept.get('home')).toEqual({ name: 'ana', folder: `${HOME}/code/mine` })
      await $.command.run(run('new-plugin', '--name bo --folder plugins/bo/'))
      expect(kept.get('home')).toEqual({ name: 'bo', folder: '/work/plugins/bo' })
    })

    test('a folder with other files in it is left untouched, and nothing runs', async ($, on) => {
      session(on)
      const files = disk(on, [`${PLUGIN}/notes.md`])
      const { ran, kept } = machine(on)
      await $.session.start(start())
      expect((await $.command.run(run('new-plugin', 'ana'))).text).toBe('not empty, left untouched: ~/ana-claude')
      expect(files.size).toBe(1)
      expect(ran).toEqual([])
      expect(kept.size).toBe(0)
    })

    test('a folder that already holds the plugin (a clone) is added and installed: only a missing marketplace.json is written, no git init', async ($, on) => {
      session(on)
      const files = disk(on, [`${PLUGIN}/README.md`], { [MANIFEST]: '{"name": "ana"}' })
      const { ran, kept } = machine(on)
      await $.session.start(start())
      expect((await $.command.run(run('new-plugin', 'ana'))).text).toBe('added your plugin ana at ~/ana-claude: /new-skill and /new-agent write there now')
      expect(files.get(`${PLUGIN}/README.md`)).toBe('')
      expect(files.has(`${PLUGIN}/.claude-plugin/marketplace.json`)).toBe(true)
      expect(files.has(`${PLUGIN}/skills/.gitkeep`)).toBe(false)
      expect(ran).toEqual([`claude plugin marketplace add ${PLUGIN}`, 'claude plugin install ana@ana --scope user'])
      expect(kept.get('home')).toEqual({ name: 'ana', folder: PLUGIN })
    })

    test('run again after it was added and installed, it runs nothing more', async ($, on) => {
      session(on)
      disk(on, [], { [MANIFEST]: '{"name": "ana"}' })
      const { ran } = machine(on, { settings: { extraKnownMarketplaces: { ana: { source: { source: 'directory', path: PLUGIN } } }, enabledPlugins: { 'ana@ana': true } } })
      await $.session.start(start())
      await $.command.run(run('new-plugin', 'ana'))
      expect(ran).toEqual([])
    })

    test('a name taken by another plugin or marketplace, or a folder holding another plugin, is refused', async ($, on) => {
      session(on)
      disk(on, [], { [`${HOME}/bo-claude/.claude-plugin/plugin.json`]: '{"name": "other"}', [`${HOME}/dee-claude/.claude-plugin/plugin.json`]: '{name' })
      machine(on, { settings: { extraKnownMarketplaces: { ana: { source: { source: 'github', repo: 'x/ana' } } }, enabledPlugins: { 'cy@somewhere': true } } })
      await $.session.start(start())
      expect((await $.command.run(run('new-plugin', 'ana'))).text).toBe('a marketplace named ana already comes from elsewhere: pick another name')
      expect((await $.command.run(run('new-plugin', 'cy'))).text).toBe('a plugin named cy is already installed (cy@somewhere): pick another name')
      expect((await $.command.run(run('new-plugin', 'bo'))).text).toBe("~/bo-claude holds the plugin 'other': name it other, or pick another folder")
      expect((await $.command.run(run('new-plugin', 'dee'))).text).toBe('cannot read ~/dee-claude/.claude-plugin/plugin.json: fix it, or pick another folder')
      expect((await $.command.run(run('new-plugin', '--name foo/bar'))).text).toBe("bad name 'foo/bar': use lowercase letters, digits and hyphens")
    })

    test('a claude command that fails stops it with its last line, and nothing is kept as the home', async ($, on) => {
      session(on)
      disk(on)
      const { kept } = machine(on, { fails: 'claude plugin marketplace' })
      await $.session.start(start())
      expect((await $.command.run(run('new-plugin', 'ana'))).text).toBe('claude plugin marketplace add failed: no such marketplace')
      expect(kept.size).toBe(0)
    })

    test('headless, nothing after the command shows the usage; at a session, the form opens', async ($, on) => {
      const { seen } = session(on)
      machine(on)
      await $.session.start(start(false))
      expect((await $.command.run(run('new-plugin', ''))).text).toBe('usage: /new-plugin <name>, or /new-plugin --name <name> --folder <folder>')
      await $.session.start(start())
      await $.command.run(run('new-plugin', ''))
      expect(seen.opened).toEqual([{ id: 'new-plugin', rows: 12, focus: true, closeOnEscape: true }])
    })

    test('the form: the folder shown follows the name, and Create plugin makes it and closes the pane', async ($, on) => {
      const { seen } = session(on)
      disk(on)
      const { kept } = machine(on)
      await $.session.start(start())
      await $.command.run(run('new-plugin', ''))
      const form = await $.ui.mount(pane('new-plugin'))
      await form.input({ key: 'name-1', text: 'ana', kind: 'change' })
      await form.redraw()
      expect((await form.find({ key: 'folder-1' }))?.props.placeholder).toBe('~/ana-claude')
      await form.press({ key: 'create' })
      expect(kept.get('home')).toEqual({ name: 'ana', folder: PLUGIN })
      expect(seen.closed).toEqual(['new-plugin'])
      expect(seen.logged).toEqual(['created your plugin ana at ~/ana-claude: /new-skill and /new-agent write there now'])
    })
  })

  describe('with a plugin of their own, /new-skill and /new-agent write into it', () => {
    test('--name writes in the plugin, without .claude/, says the name Claude Code knows it by, and reloads plugins', async ($, on) => {
      const { seen, clock } = session(on)
      const files = disk(on, [MANIFEST])
      machine(on, HAS_PLUGIN)
      await $.session.start(start())
      const { text, context } = await $.command.run(run('new-skill', '--name release-notes'))
      expect(text).toBe('created ~/ana-claude/skills/release-notes/SKILL.md, in your plugin: ana:release-notes')
      expect(files.get(`${PLUGIN}/skills/release-notes/SKILL.md`)).toContain('name: release-notes\n')
      expect(context?.[0]).toContain(`It is in the user's own plugin, ana (${PLUGIN}), so Claude Code knows it as ana:release-notes`)
      await clock.settle()
      expect(seen.ran).toEqual(['/reload-plugins '])
    })

    test('--where project writes under the working directory, as without a plugin', async ($, on) => {
      session(on)
      const files = disk(on, [MANIFEST])
      machine(on, HAS_PLUGIN)
      await $.session.start(start())
      expect((await $.command.run(run('new-agent', '--name code-reviewer --where project'))).text).toBe('created .claude/agents/code-reviewer.md')
      expect(files.has('.claude/agents/code-reviewer.md')).toBe(true)
    })

    test('--where plugin with no plugin, or a --where it does not know, writes nothing', async ($, on) => {
      session(on)
      const files = disk(on)
      machine(on)
      await $.session.start(start())
      expect((await $.command.run(run('new-agent', '--name x --where plugin'))).text).toBe('no plugin of yours yet: /new-plugin makes one')
      expect((await $.command.run(run('new-agent', '--name x --where there'))).text).toBe("--where takes plugin or project, not 'there'")
      expect(files.size).toBe(0)
    })

    test('a plugin whose folder no longer holds it is forgotten: the file goes under the working directory', async ($, on) => {
      session(on)
      const files = disk(on)
      machine(on, HAS_PLUGIN)
      await $.session.start(start())
      await $.command.run(run('new-skill', '--name release-notes'))
      expect(files.has('.claude/skills/release-notes/SKILL.md')).toBe(true)
    })

    test('free text asks Claude, the words saying where in the plugin the file goes', async ($, on) => {
      const { seen, clock } = session(on)
      disk(on, [MANIFEST])
      machine(on, HAS_PLUGIN)
      await $.session.start(start())
      await $.command.run(run('new-agent', 'a reviewer for pull requests'))
      await clock.settle()
      expect(seen.ran).toEqual([`/agentic-engineering:agent-development a reviewer for pull requests (put it in my plugin ana: ${PLUGIN}/agents/<name>.md)`])
    })

    test('the form has a Where row, the plugin picked; picking this project writes under the working directory', async ($, on) => {
      const { seen } = session(on)
      const files = disk(on, [MANIFEST])
      machine(on, HAS_PLUGIN)
      await $.session.start(start())
      await $.command.run(run('new-skill', ''))
      expect(seen.opened).toEqual([{ id: 'new-skill', rows: 13, focus: true, closeOnEscape: true }])
      const form = await $.ui.mount(pane('new-skill'))
      const where = await form.find({ key: 'where-1' })
      expect(where?.props.value).toBe('plugin')
      expect(await form.find({ text: 'Writes ~/ana-claude/skills/<name>/SKILL.md from the skill template.' })).toBeDefined()
      await form.select({ key: 'where-1', value: 'project' })
      await form.input({ key: 'name-1', text: 'release-notes', kind: 'change' })
      await form.press({ key: 'create' })
      expect(files.has('.claude/skills/release-notes/SKILL.md')).toBe(true)
      expect(files.has(`${PLUGIN}/skills/release-notes/SKILL.md`)).toBe(false)
    })
  })
})
