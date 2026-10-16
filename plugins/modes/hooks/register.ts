// The plugin's one hooks module (hooks.json takes one, and each event once): the session modes, all
// registered by mode.ts, and /ask's refusal of the file tools.
import type { Register } from 'claude-code'

import { ASK, refuseEdits } from './ask'
import { modes } from './mode'
import { ORCHESTRATE } from './orchestrate'

export const register: Register = on => {
  modes(on, [ORCHESTRATE, ASK])
  refuseEdits(on)
}
