/**
 * What has to be true before anything else in this process reads a module.
 *
 * Side-effecting on import, on purpose, and imported FIRST by
 * `bin/brain-mcp.ts` — because both things it does are only correct before
 * other modules are evaluated:
 *
 *   the node floor   `memdb.ts` imports `node:sqlite`, which is unflagged only
 *                    from 22.13. Below that the import itself throws, so a
 *                    check in the entry BODY would never run: a person would
 *                    get a stack trace about a builtin module instead of one
 *                    line naming the version they need. Nothing here imports
 *                    anything but brand.ts, which imports only long-stable
 *                    builtins, so this module is safe on any node that can
 *                    parse it.
 *   the --root flag  `paths.ts` computes `BRAIN` as a load-time const from
 *                    `$AK_PATH` and the walk up from cwd. Setting the env
 *                    var after that const is evaluated changes nothing, so the
 *                    flag has to be read before paths.ts is.
 *
 * In the BUNDLE the same two things are in esbuild's banner (build.mjs), which
 * is the only place that provably runs before every inlined dependency. The
 * duplication is deliberate: the banner covers the shipped artefact whatever
 * order esbuild chooses, this module covers `node bin/brain-mcp.ts` run from
 * source. Both are idempotent — the env var gets the same value twice and the
 * version check exits rather than falling through.
 */

import { DISPLAY_NAME, envName } from "./brand.ts" // only stable builtins behind it, so it is safe before the floor check

/** `node:sqlite` is unflagged from here. The whole reason the plugin declares
 *  a node floor at all, in four places that must agree: this file, `bin/_launch.mjs` (FLOOR),
 *  `bin/find-node.sh` (FIND_NODE_MIN) and `package.json` engines. */
export const NODE_FLOOR = [22, 13] as const

const parts = process.versions.node.split(".").map((n) => Number.parseInt(n, 10))
const major = parts[0] ?? 0
const minor = parts[1] ?? 0

if (major < NODE_FLOOR[0] || (major === NODE_FLOOR[0] && minor < NODE_FLOOR[1])) {
  // ONE line, never a stack: stdout is the MCP transport and a person reading
  // a failed server sees only stderr.
  process.stderr.write(
    `[${DISPLAY_NAME}] node ${process.versions.node} is below the required ${NODE_FLOOR.join(".")} (node:sqlite) — the ${DISPLAY_NAME} tools cannot start\n`,
  )
  process.exit(1)
}

/**
 * `--root <path>` pins the vault instead of walking up from `process.cwd()`.
 *
 * The walk is the default because Claude Code spawns an MCP server with cwd
 * set to the session's launch directory, which is exactly the directory whose
 * nearest `.ak/` the session should get. The flag is for the other callers:
 * `ak mcp` from anywhere, a hand-typed path, a test.
 */
const at = process.argv.indexOf("--root")
if (at > 0 && process.argv[at + 1]) process.env[envName("PATH")] = process.argv[at + 1]
