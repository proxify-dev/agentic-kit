import { dirname, join, resolve } from "node:path"
import { existsSync, realpathSync } from "node:fs"
import { homedir } from "node:os"
import { HOME_DIR, claudeHome, env } from "./brand.ts"

/**
 * A vault is any directory carrying the manifest — that is the whole marker.
 *
 * `~/.ak` used to be the only answer, hard-coded on this line and eleven
 * more times in the plugin's Python. The global vault is now just the LAST
 * answer: a directory that carries `.ak/`, or that IS a vault, is a scope, so
 * a repo can keep its memory beside its code and a session launched inside it
 * files there instead of in the one global vault.
 *
 * `scopes()` is nearest-first and nothing chains yet — `brainRoot()` takes the
 * first and that is the vault for the process. An ancestor that IS a vault
 * counts as well as one that CONTAINS `.ak/`, which is what makes a session
 * launched from `<repo>/.ak/projects` resolve to `<repo>/.ak` rather than
 * walking past it.
 * The global vault is the last rung or nothing — the walk never counts it, nor
 * `~/.ak` and `~/.ak/vault`, as a project scope — so a sandbox with its own
 * $AK_HOME never sees the person's real global vault (vault_root.py's rule).
 *
 * The kit home (layout 2, the vault engine's ladder.py names every folder):
 *
 *   <kit>/vault/   the global vault: its manifest, the notes, _templates
 *   <kit>/db/      what can't be rebuilt: brain.db, traces/, the ledgers
 *   <kit>/cache/   what rebuilds itself when deleted
 *
 * <kit> is $AK_HOME, else $AK_PATH (a sandbox that pins only the vault keeps
 * its state beside it, sealed), else `~/.ak`.
 *
 * `$AK_PATH` short-circuits the walk and is passed through RAW, unresolved.
 * That is deliberate: the selftests set it to a `mkdtemp` fixture and then
 * assert `BRAIN === fixture`, and on macOS realpath answers `/private/var/…`
 * for exactly that path. Everything that must survive a symlink is realpath'd
 * where it is USED (`realish`, `vaultRelative`), so normalising here would buy
 * nothing and break that identity. The kit home is passed through the same way.
 */
export const VAULT_MARKER = "vault-manifest.json"

export const isVault = (d: string): boolean => existsSync(join(d, VAULT_MARKER))

/** The kit home, where its state lives: $AK_HOME, else $AK_PATH, else `~/.ak`. */
export const kitHome = (): string => env("HOME")?.trim() || env("PATH")?.trim() || join(homedir(), HOME_DIR)

/** The global vault: $AK_PATH, else the kit home's `vault/` — the tail of every chain. */
export const globalVault = (): string => env("PATH")?.trim() || join(kitHome(), "vault")

/** realpath, or the absolute path itself when nothing is there to resolve. */
const realOrRaw = (p: string): string => {
  try {
    return realpathSync(p)
  } catch {
    return resolve(p)
  }
}

export function scopes(cwd?: string): string[] {
  const pinned = env("PATH")
  if (pinned) return [pinned]
  const home = realOrRaw(globalVault())
  const kit = new Set([home, realOrRaw(join(homedir(), HOME_DIR)), realOrRaw(join(homedir(), HOME_DIR, "vault"))])
  const found: string[] = []
  let d = realOrRaw(cwd ?? process.cwd())
  for (;;) {
    const nested = join(d, HOME_DIR)
    if (isVault(nested)) {
      if (!kit.has(nested)) found.push(nested)
    } else if (isVault(d) && !kit.has(d)) found.push(d)
    const parent = dirname(d)
    if (parent === d) break
    d = parent
  }
  if (!found.includes(home)) found.push(home)
  return [...new Set(found)]
}

/** The vault this process reads and writes: the nearest scope. */
export const brainRoot = (cwd?: string): string => scopes(cwd)[0]

/**
 * Resolved once, at module load. The stdio door is one process per session and
 * the app one per window, so the ~15 call sites that read this never need it to
 * move under them.
 */
export const BRAIN = brainRoot()
/** Claude Code's config folder, through the seam, so it honours $CLAUDE_CONFIG_DIR. */
export const HOME_CLAUDE = claudeHome()
/** The kit home, resolved once like BRAIN: every store below hangs off it. */
export const KIT_HOME = kitHome()

/**
 * The event store — what the machine remembers (docs/store.md). Sessions,
 * prompts, observations and summaries as rows, claude-mem's schema 1:1, written
 * by the ak plugin's summariser and only ever READ here.
 *
 * `memoryDb()` resolves at open time, not at import: a machine that has not run
 * `ak observer store init` keeps reading claude-mem's file, and the first init flips
 * every reader at once without a restart.
 */
/** The event store, the trace store and every ledger: one per kit home, not one per scope. */
export const STORE_DIR = env("STORE")?.trim() || join(KIT_HOME, "db")
export const STORE_DB = join(STORE_DIR, "brain.db")
/** What rebuilds itself when deleted. */
export const CACHE_DIR = join(KIT_HOME, "cache")
export const CLAUDE_MEM_DB = join(homedir(), ".claude-mem", "claude-mem.db")
export const memoryDb = (): string => (existsSync(STORE_DB) ? STORE_DB : CLAUDE_MEM_DB)
