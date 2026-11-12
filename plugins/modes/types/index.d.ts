/**
 * One session mode's state: whether it is on, at which level (none for plain `on`; `lead` for
 * `/orchestrate lead`), and whether its rules are owed again after a compaction.
 */
export type ModeState = { sessionId: string; isOn: boolean; level?: string; needsRules: boolean }

declare module 'claude-code' {
  interface PluginState {
    /** Each mode's state by its name (`orchestrate`, `ask`); none while the mode has not been read this process. */
    'modes': { modes: Record<string, ModeState | undefined> }
  }
}
