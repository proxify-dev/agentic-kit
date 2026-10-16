// /orchestrate [on|off|status]: this session's delegation mode. The switch, its reminders and its chip are
// mode.ts; the words are prompts/*.orchestrate.md.
import type { ModeSpec } from './mode'

export const ORCHESTRATE: ModeSpec = {
  name: 'orchestrate',
  label: 'Orchestration',
  chip: 'orchestrating',
  description: 'Delegation mode for this session: on, off or status; nothing toggles',
}
