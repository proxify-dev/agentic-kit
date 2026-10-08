// The one launcher both Node entries share: `node plugins/tracer/traces/bin/traces.mjs` beside it and
// the app's `node app/core/bin/brain-mcp.mjs`, which passes its own root. No shell, so it starts the same way on macOS,
// Linux and Windows; whoever runs `node` already chose the interpreter, so the old
// find-node.sh lookup has nothing left to do here but check its version.
//
// Plain JavaScript on purpose: it must parse on ANY node, including one below the
// floor, to say so in one line instead of a stack about `node:sqlite`.
//
// STDOUT BELONGS TO THE BUNDLE (the MCP protocol, or the one JSON document). Every
// diagnostic is one line on stderr, and nothing is written to fd 1 before the import.
//
//   exit 66   the bundle is absent: not built, or a partial install
//   exit 127  node is below 22.13
//
// <ENV_PREFIX><debug>=1 names the running node and the bundle on stderr, then continues.

import { existsSync, readFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

/** `node:sqlite` is unflagged from here. Agrees with src/preflight.ts and package.json engines. */
const FLOOR = [22, 13]

/** This tree (plugins/tracer/traces/): an entry's root when it names none. */
const HERE = dirname(dirname(fileURLToPath(import.meta.url)))

/** The env prefix from the generated src/brand.ts. This file cannot import a .ts, and a
 *  brand word is never typed; a checkout without src/ simply has no debug switch. */
function envPrefix(root) {
  try {
    return /export const ENV_PREFIX = "([^"]+)"/.exec(readFileSync(join(root, "src", "brand.ts"), "utf8"))?.[1]
  } catch {
    return undefined
  }
}

/**
 * @param {string} name   the entry: dist/<name>.mjs is what runs
 * @param {{ debug: string, home?: boolean, root?: string }} opts
 *   debug  the env suffix that turns the stderr line on (MCP_DEBUG, TRACES_DEBUG)
 *   root   the tree holding dist/<name>.mjs and src/brand.ts (default: this one)
 *   home   run from the home directory, so BRAIN resolves the same from every session. It
 *          was load-bearing for traces while their store hung off BRAIN (a project carrying its
 *          own vault got a trace store of its own); since layout 2 it hangs off the kit home
 *          (src/paths.ts: STORE_DIR) and the cwd no longer moves it.
 */
export async function launch(name, { debug, home = false, root = HERE }) {
  const [major, minor] = process.versions.node.split(".").map((n) => Number.parseInt(n, 10))
  if (major < FLOOR[0] || (major === FLOOR[0] && minor < FLOOR[1])) {
    process.stderr.write(
      `${name}: node ${process.versions.node} at ${process.execPath} is below the required ${FLOOR.join(".")} (node:sqlite)\n`,
    )
    process.exit(127)
  }

  const bundle = join(root, "dist", `${name}.mjs`)
  const prefix = envPrefix(root)
  if (prefix && process.env[prefix + debug]) {
    process.stderr.write(`${name}: node ${process.versions.node} at ${process.execPath} · bundle ${bundle}\n`)
  }
  if (!existsSync(bundle)) {
    process.stderr.write(`${name}: no bundle at ${bundle} — build it (npm run build in ${root})\n`)
    process.exit(66)
  }

  // What --no-warnings did for the shell launcher: node:sqlite warns on some versions,
  // and stderr is a protocol log. The bundle's banner does the same; both are idempotent.
  process.removeAllListeners("warning")
  if (home) process.chdir(homedir())
  // argv is left as it came: the bundle's entry check reads argv[1] (…/<name>.mjs) and
  // its --root banner reads the rest.
  await import(pathToFileURL(bundle).href)
}
