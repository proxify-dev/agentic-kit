/**
 * Does Claude Code keep a transcript of this session? At `capture: gaps` the
 * gateway saves a call's body only when none will: a transcript already holds
 * every message, the whole system prompt and every tool
 * (poc/wire-research/2026-09-25). Claude Code writes
 * `<config>/projects/<cwd-slug>/<session>.jsonl` before its first model call
 * (1,678 of 1,678 sessions measured), so the file's presence when the call
 * arrives is the answer — a subagent's call carries its parent's session, whose
 * transcript holds the subagent's as well.
 */
import { existsSync, readdirSync } from "node:fs"
import { join } from "node:path"
import { claudeHome } from "./brand.ts"

const SESSION = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
/** A "no" is asked again after this long; a "yes" never changes. */
const RECHECK_MS = 30_000

export class Transcripts {
  #projects: string
  #yes = new Set<string>()
  #no = new Map<string, number>()

  constructor(configDir = claudeHome()) {
    this.#projects = join(configDir, "projects")
  }

  /** False for a missing or malformed session id — the header is the caller's, never a path. */
  has(session: string | null | undefined, now = Date.now()): boolean {
    if (!session || !SESSION.test(session)) return false
    if (this.#yes.has(session)) return true
    const asked = this.#no.get(session)
    if (asked !== undefined && now - asked < RECHECK_MS) return false
    let dirs: string[] = []
    try {
      dirs = readdirSync(this.#projects)
    } catch {
      // no projects dir: nothing is recorded here
    }
    if (dirs.some((d) => existsSync(join(this.#projects, d, `${session}.jsonl`)))) {
      this.#yes.add(session)
      this.#no.delete(session)
      return true
    }
    this.#no.set(session, now)
    return false
  }
}
