// /orchestrate [on|lead|off|status]: this session's delegation mode. The switch, its reminders and its chip
// are mode.ts; what Claude reads is prompts/*.orchestrate.md (on) and prompts/*.orchestrate-lead.md (lead);
// what the person reads under each answer is `about` below.
//
//   on    the main session does most of the work; an independent part whose outcome is all it needs goes
//         to a fresh agent with its model set, or to a fork when it needs the conversation
//   lead  the main session plans and decides, agents do the work: the same split, for anything whose
//         outcome is all it needs, so tool output stays out of the main context
//
// A fork runs on the session's own model and re-reads the whole conversation on every step: in both levels
// it is only for work that needs the conversation.
import type { ModeSpec } from './mode'

export const ORCHESTRATE: ModeSpec = {
  name: 'orchestrate',
  label: 'Orchestration',
  chip: 'orchestrating',
  description: 'Hand work to subagents, this session: on · lead (Claude plans, agents do the work) · off · status',
  about: {
    on: {
      short: 'Claude works itself, hands off parts that stand alone',
      now: 'Claude does most of the work; parts that stand alone go to agents on cheaper models',
      see: 'now and then an agent in the background, its result arriving as a notification',
    },
    off: {
      short: 'no delegation rules',
      now: 'Claude works as usual; agents already running may finish',
    },
  },
  levels: {
    lead: {
      chip: 'orchestrating · lead',
      about: {
        short: 'Claude plans and decides; agents do the work',
        now: 'Claude plans and decides; searches, reading, tests and planned edits go to agents',
        see: 'agents on haiku or sonnet in the background; a fork only when it needs the chat',
      },
    },
  },
}
