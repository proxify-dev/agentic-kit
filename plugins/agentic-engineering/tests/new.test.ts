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
}

// No disk in a test: these hooks stand in for it, holding what was written and answering the templates. The
// engine hands them the path made absolute under the working directory; they key it from its last `.claude/` on,
// since the working directory can hold one itself (a worktree under .claude/worktrees/).
const disk = (on: On, present: string[] = []) => {
  const files = new Map<string, string>(present.map(p => [p, '']))
  const rel = (path: string) => path.slice(path.lastIndexOf('.claude/'))
  on('fs.read', ($, e, next) => {
    const template = Object.entries(TEMPLATES).find(([path]) => e.path.endsWith(path))
    return template ? { value: template[1] } : next(e)
  })
  on('fs.exists', ($, e) => ({ value: files.has(rel(e.path)) }))
  on('fs.write', ($, e) => {
    files.set(rel(e.path), e.text)
    return { value: undefined }
  })
  return files
}

const SKILLS = ['agentic-engineering:agent-development', 'agentic-engineering:skill-development']

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
  test('registers /new-agent and /new-skill as immediate commands', async ($, on) => {
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
})
