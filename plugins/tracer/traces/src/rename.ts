import { renameSync } from "node:fs"

/**
 * The rename of a temp-and-rename write. Windows refuses to replace a file
 * another process holds open — a reader, the search indexer, an antivirus
 * scan — with EPERM, EBUSY or EACCES for a moment; there it is retried five
 * times, 50 ms apart. POSIX renames once, as it always did.
 */
export function renameRetrySync(
  from: string,
  to: string,
  plat: NodeJS.Platform = process.platform,
  rename: (a: string, b: string) => void = renameSync,
): void {
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
