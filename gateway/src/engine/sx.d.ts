// Declarations for the vendored sx.js — see types.d.ts.

export class SxManager {
  constructor(opts?: { log?: (msg: string) => void })
  apiKey: string | null
  mode: string
  configure(apiKey: string, mode?: string): Promise<{ ok: boolean; error?: string }>
  setMode(mode: string): Promise<void>
  disable(): void
}
