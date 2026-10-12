/**
 * Owner-only files, on every OS. POSIX has the mode bits the writers already
 * pass (0o600 files, 0o700 dirs). Windows ignores them, so a folder of ours
 * that holds tokens or prompts also gets an ACL that grants its owner alone,
 * inherited by every file made in it — the temp-and-rename writes included,
 * which is why it is done to the folder and not to each file. A folder outside
 * ours (an accounts file someone pointed elsewhere) is left as it is; the file
 * gets the ACL instead. Best effort: a failed `icacls` leaves the default ACL,
 * which on a normal profile is already the user's own.
 */
import { spawnSync } from "node:child_process"
import { mkdirSync, renameSync } from "node:fs"
import { userInfo } from "node:os"
import { relative, isAbsolute } from "node:path"
import { configDir, dataDir, isWindows } from "./brand.ts"

const done = new Set<string>()

/**
 * The temp-and-rename's rename. Windows refuses to replace a file another
 * process has open (a reader, the indexer, an antivirus scan) with EPERM or
 * EBUSY for a moment: retried there, five times 50 ms apart. POSIX renames at once.
 */
export function renameRetrySync(from: string, to: string, plat: NodeJS.Platform = process.platform, rename: (a: string, b: string) => void = renameSync): void {
  for (let i = 0; ; i++) {
    try {
      return rename(from, to)
    } catch (err) {
      const code = (err as NodeJS.ErrnoException).code
      if (plat !== "win32" || i >= 5 || (code !== "EPERM" && code !== "EBUSY" && code !== "EACCES")) throw err
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 50)
    }
  }
}

/** `DOMAIN\user`, the principal icacls grants. */
export function windowsPrincipal(env: NodeJS.ProcessEnv = process.env): string {
  const user = env.USERNAME ?? userInfo().username
  return env.USERDOMAIN ? `${env.USERDOMAIN}\\${user}` : user
}

/** The icacls arguments that make `path` its owner's alone; `dir` makes the grant inherit. */
export function icaclsArgs(path: string, principal: string, dir: boolean): string[] {
  return [path, "/inheritance:r", "/grant:r", `${principal}:${dir ? "(OI)(CI)F" : "F"}`]
}

/** Strictly inside one of our roots — never the root itself, never someone else's folder. */
export function ours(dir: string, roots: string[] = [configDir(), dataDir()]): boolean {
  return roots.some((r) => {
    const rel = relative(r, dir)
    return rel !== "" && !rel.startsWith("..") && !isAbsolute(rel)
  })
}

function icacls(path: string, dir: boolean): void {
  spawnSync("icacls", icaclsArgs(path, windowsPrincipal(), dir), { stdio: "ignore", windowsHide: true })
}

/** mkdir -p with 0o700, and on Windows, for a folder of ours, an owner-only inherited ACL. */
export function privateDir(dir: string): void {
  mkdirSync(dir, { recursive: true, mode: 0o700 })
  if (!isWindows() || done.has(dir) || !ours(dir)) return
  done.add(dir)
  icacls(dir, true)
}

/** A file just written with mode 0o600, in a folder that may not be ours: on Windows, its own ACL. */
export function privateFile(file: string, dir: string): void {
  if (isWindows() && !ours(dir)) icacls(file, false)
}
