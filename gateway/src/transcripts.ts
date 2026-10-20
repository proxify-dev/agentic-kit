/**
 * What Claude Code keeps on disk about a session, read for the gateway's own use.
 *
 * Does it keep a transcript? At `capture: gaps` the gateway saves a call's body
 * only when none will: a transcript already holds every message, the whole
 * system prompt and every tool (poc/wire-research/2026-09-25). Claude Code writes
 * `<config>/projects/<cwd-slug>/<session>.jsonl` before its first model call
 * (1,678 of 1,678 sessions measured), so the file's presence when the call
 * arrives is the answer — a subagent's call carries its parent's session, whose
 * transcript holds the subagent's as well.
 *
 * The sessions page (sessions.ts) reads two more things from the same folder,
 * never a message: the session's title — the newest `custom-title` (a /rename)
 * or `ai-title` line in the transcript — and each subagent's
 * `<session>/subagents/agent-<agentId>.meta.json`: its type, the task's
 * one-line description, whether it has finished.
 */
import { closeSync, existsSync, openSync, readFileSync, readSync, readdirSync, statSync } from "node:fs"
import { join } from "node:path"
import { claudeHome } from "./brand.ts"

const SESSION = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
/** A subagent id that can be a file name: no dot, no slash. */
const AGENT_FILE_ID = /^[A-Za-z0-9_-]{1,80}$/
/** A "no" is asked again after this long; a "yes" never changes. */
const RECHECK_MS = 30_000
/**
 * For the sessions view, a "no" holds longer: a session run with another Claude
 * config folder (an eval sandbox) is in none of these, and each look at it tries
 * every folder — on the thread that also carries the wire.
 */
const VIEW_RECHECK_MS = 10 * 60_000
/** A title line sits near the top and is repeated later: a first look reads this much of each end. */
const TITLE_SPAN = 256 * 1024

export type SubagentMeta = {
  agentType: string | null
  description: string | null
  model: string | null
  finished: boolean | null
}

type TitleScan = { scanned: number; title: string | null; custom: boolean }

const text = (v: unknown, max: number): string | null =>
  typeof v === "string" && v.trim() ? v.trim().replace(/[\u0000-\u001f\u007f]/g, " ").slice(0, max) : null

export class Transcripts {
  #projects: string
  /** session → the project folder holding its transcript. */
  #dirs = new Map<string, string>()
  #no = new Map<string, number>()
  #viewNo = new Map<string, number>()
  #listing: { at: number; names: string[] } | null = null
  #titles = new Map<string, TitleScan>()
  /** Finished subagents only: their meta never changes again. */
  #done = new Map<string, SubagentMeta>()

  constructor(configDir = claudeHome()) {
    this.#projects = join(configDir, "projects")
  }

  /** False for a missing or malformed session id — the header is the caller's, never a path. */
  has(session: string | null | undefined, now = Date.now(), project: string | null = null): boolean {
    return this.dirOf(session, now, project) !== null
  }

  /**
   * The project folder that holds this session's transcript, or null. `project`
   * (the cwd's last part, x-brain-project) is a hint: Claude Code names the folder
   * after the cwd, so the folders ending in it are tried first — one machine had
   * 2,652 project folders, and a blind look tries each.
   */
  dirOf(session: string | null | undefined, now = Date.now(), project: string | null = null): string | null {
    return this.#find(session, now, project, this.#no, RECHECK_MS, true)
  }

  /** dirOf for the sessions view: a listing a second old will do, and a "no" holds ten minutes. */
  #viewDir(session: string, project: string | null): string | null {
    return this.#find(session, Date.now(), project, this.#viewNo, VIEW_RECHECK_MS, false)
  }

  #find(session: string | null | undefined, now: number, project: string | null, no: Map<string, number>, recheck: number, fresh: boolean): string | null {
    if (!session || !SESSION.test(session)) return null
    const known = this.#dirs.get(session)
    if (known) return known
    const asked = no.get(session)
    if (asked !== undefined && now - asked < recheck) return null
    const tail = project ? `-${project.replace(/[^A-Za-z0-9]/g, "-")}` : null
    const holds = (d: string) => existsSync(join(this.#projects, d, `${session}.jsonl`))
    const names = this.#folders(now, fresh)
    const name = (tail && names.find((d) => d.endsWith(tail) && holds(d))) || names.find(holds)
    if (name) {
      const dir = join(this.#projects, name)
      this.#dirs.set(session, dir)
      no.delete(session)
      return dir
    }
    no.set(session, now)
    return null
  }

  /** The project folders' names; `fresh: false` takes one read in the last second. */
  #folders(now: number, fresh: boolean): string[] {
    if (!fresh && this.#listing && now - this.#listing.at < 1000) return this.#listing.names
    let names: string[] = []
    try {
      names = readdirSync(this.#projects)
    } catch {
      // no projects dir: nothing is recorded here
    }
    this.#listing = { at: now, names }
    return names
  }

  /**
   * The session's title: its newest `/rename`, else Claude Code's own ai-title.
   * A first look reads the transcript's two ends; after that only what was
   * appended since, so asking every refresh costs a stat.
   */
  title(session: string, project: string | null = null): string | null {
    const dir = this.#viewDir(session, project)
    if (!dir) return null
    const file = join(dir, `${session}.jsonl`)
    let size: number
    try {
      size = statSync(file).size
    } catch {
      return null
    }
    let scan = this.#titles.get(session)
    if (scan && scan.scanned === size) return scan.title
    if (!scan || size < scan.scanned) {
      scan = { scanned: 0, title: null, custom: false }
      this.#readTitles(file, 0, Math.min(size, TITLE_SPAN), scan)
      scan.scanned = Math.max(scan.scanned, size - TITLE_SPAN)
    }
    this.#readTitles(file, scan.scanned, size, scan)
    this.#titles.set(session, scan)
    return scan.title
  }

  /** Reads [from, to), takes every title line in it, and moves `scanned` past the last whole line. */
  #readTitles(file: string, from: number, to: number, scan: TitleScan): void {
    if (to <= from) return
    const buf = Buffer.alloc(to - from)
    let fd: number | null = null
    try {
      fd = openSync(file, "r")
      readSync(fd, buf, 0, buf.length, from)
    } catch {
      return
    } finally {
      if (fd !== null) closeSync(fd)
    }
    const s = buf.toString("utf8")
    const end = s.lastIndexOf("\n")
    if (end < 0) return
    for (const line of s.slice(0, end).split("\n")) {
      if (!line.includes('-title"')) continue
      let row: { type?: string; customTitle?: unknown; aiTitle?: unknown }
      try {
        row = JSON.parse(line)
      } catch {
        continue // the first line of a range read from the middle is cut
      }
      const custom = row.type === "custom-title" ? text(row.customTitle, 120) : null
      const ai = row.type === "ai-title" ? text(row.aiTitle, 120) : null
      if (custom) Object.assign(scan, { title: custom, custom: true })
      else if (ai && !scan.custom) scan.title = ai
    }
    scan.scanned = from + Buffer.byteLength(s.slice(0, end + 1))
  }

  /** One subagent's meta file, or null when Claude Code wrote none (yet). */
  subagent(session: string, agentId: string, project: string | null = null): SubagentMeta | null {
    if (!AGENT_FILE_ID.test(agentId)) return null
    const key = `${session}/${agentId}`
    const done = this.#done.get(key)
    if (done) return done
    const dir = this.#viewDir(session, project)
    if (!dir) return null
    let raw: Record<string, unknown>
    try {
      raw = JSON.parse(readFileSync(join(dir, session, "subagents", `agent-${agentId}.meta.json`), "utf8"))
    } catch {
      return null
    }
    const meta: SubagentMeta = {
      agentType: text(raw.agentType, 60),
      description: text(raw.description, 200),
      model: text(raw.model, 60),
      finished: typeof raw.finished === "boolean" ? raw.finished : null,
    }
    if (meta.finished) this.#done.set(key, meta)
    return meta
  }
}
