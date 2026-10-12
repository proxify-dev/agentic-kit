// Declarations for the vendored config.js — see types.d.ts.
import type { AccountsFile } from "./types.d.ts"

export function getConfigPath(): string
export function getStatePath(): string
export function loadConfig(): Promise<AccountsFile | null>
export function saveConfig(config: AccountsFile): Promise<void>
export function atomicConfigUpdate(updater: (config: AccountsFile) => void | Promise<void>): Promise<AccountsFile>
export function loadState(): Promise<{ quota?: unknown } | null>
export function saveState(state: { quota: unknown }): Promise<void>
/** fsync'd temp file + rename; follows a symlinked path; 0600. */
export function writeJsonAtomic(path: string, value: unknown): Promise<void>
/** Run `fn` holding `<configPath>.lock` — the advisory lock every writer of the file honours. */
export function withConfigLock<T>(configPath: string, fn: () => T | Promise<T>): Promise<T>
/** rename, retried 5 x 50 ms on EPERM/EBUSY/EACCES on Windows; one rename on POSIX. */
export function renameRetry(from: string, to: string, plat?: NodeJS.Platform, doRename?: (a: string, b: string) => Promise<void>): Promise<void>
