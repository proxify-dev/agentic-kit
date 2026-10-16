// /ask [on|off|status]: this session answers and changes nothing. The switch, its reminders and its chip
// are mode.ts; the words are prompts/*.ask.md.
//
// The rules ask for no change of any kind. The file tools are also refused here, in every loop (a
// subagent's too), with prompts/before-edit.ask.md as the reason: a Bash command that writes is not, since
// telling one from a read takes more than its text.
import type { On } from 'claude-code'

import { isOn, words } from './mode'
import type { ModeSpec } from './mode'

export const ASK: ModeSpec = {
  name: 'ask',
  label: 'Ask mode',
  chip: 'ask mode',
  description: 'Answer only, change nothing, for this session: on, off or status; nothing toggles',
}

const EDITS = new Set(['Edit', 'Write', 'NotebookEdit'])

export const refuseEdits = (on: On) => {
  on('tool.call', async ($, e, next) => {
    if (!EDITS.has(e.tool) || !(await isOn($, ASK))) return next(e)
    return { deny: await words($, ASK.name, 'before-edit') }
  }).catch(($, e, next) => next(e))
}
