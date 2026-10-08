import { build, transformSync } from "esbuild"
import { mkdirSync, readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

/**
 * The trace indexer's bundle. Run: npm run build (from plugins/tracer/traces/)
 *
 * One file per entry, COMMITTED, run by node with no node_modules anywhere
 * near it. That is the whole point: a person installs the plugin from a
 * marketplace and the kit tools work — no npm install, no build step, no
 * uv, no app. `packages: "bundle"` is what makes that true, and it is why the
 * MCP SDK and zod are inlined rather than left as imports.
 *
 *   dist/traces.mjs   the tool-call index: the Stop hook's one-shot and the
 *                     reads of `ak tracer calls` / `ak trace` (bin/traces)
 *
 * The app's terminal door (brain-mcp) has its own build.mjs beside its source;
 * the two are one recipe and must stay so — `bundle-fresh` runs both.
 *
 * `external: ["node:*"]` is the other half: builtins stay imports, so
 * `node:sqlite` resolves against whatever node is actually running and the
 * version floor stays a runtime fact instead of something frozen at build time.
 *
 * `AK_BUNDLE_DIR=<dir>` writes every entry to `<dir>/.fresh-check-<name>.mjs`
 * instead and says nothing on stdout. That is how the app's `test/bundle-fresh.ts`
 * rebuilds and diffs — it runs THIS file rather than re-declaring the options,
 * because a freshness test that guessed at the settings would report drift
 * that was only a difference of opinion about the build.
 */

const HERE = dirname(fileURLToPath(import.meta.url))

// The brand words come from src/brand.ts. This file is plain JS, run as `node build.mjs` with no flag on any node
// from 22.13, so it cannot import a .ts file: esbuild strips the types and the module loads from a data: URL.
const { ENV_PREFIX, env } = await import(
  "data:text/javascript;base64," +
    Buffer.from(
      transformSync(readFileSync(join(HERE, "src", "brand.ts"), "utf8"), { loader: "ts", format: "esm" }).code,
    ).toString("base64"),
)
const ENTRIES = ["traces"]

/**
 * BOTH of these have to be in the BANNER, not in the entry's body.
 *
 * In a bundle the entry's own statements run LAST — after every module it
 * imports has been evaluated. So by the time `bin/traces.ts`'s first line
 * would run, `paths.ts` has already computed `BRAIN` as a load-time const and
 * `node:sqlite` has already emitted its experimental warning. The banner is
 * the only code that runs before all of that.
 *
 *   removeAllListeners('warning')   node:sqlite warns on stderr on some
 *                                   versions. Harmless to a human, noise in a
 *                                   protocol log, and the launcher already
 *                                   passes --no-warnings for everything else.
 *   --root <path>                   pins the vault instead of walking up from
 *                                   process.cwd(). Claude Code spawns an MCP
 *                                   server with cwd = the session's launch
 *                                   directory, which is exactly the directory
 *                                   whose nearest `.ak/` that session
 *                                   should get — so the walk is the default
 *                                   and the flag is for `ak mcp` and tests.
 *
 * `src/preflight.ts` does the same two things, plus the node floor, for
 * `node bin/traces.ts` run from source, where being the entry's first
 * import puts it first honestly. Both are idempotent.
 */
const BANNER =
  "process.removeAllListeners('warning');" +
  `{const i=process.argv.indexOf('--root');if(i>0&&process.argv[i+1])process.env.${ENV_PREFIX}PATH=process.argv[i+1];}`

const freshDir = env("BUNDLE_DIR")
const quiet = Boolean(freshDir)
// `node build.mjs traces` builds one entry; no argument builds them all.
const only = process.argv.slice(2)
const unknown = only.filter((n) => !ENTRIES.includes(n))
if (unknown.length) {
  console.error(`[build] no entry ${unknown.join(", ")} — the entries are ${ENTRIES.join(", ")}`)
  process.exit(64)
}

for (const name of ENTRIES.filter((n) => !only.length || only.includes(n))) {
  const outfile = freshDir
    ? join(freshDir, `.fresh-check-${name}.mjs`)
    : join(HERE, "dist", `${name}.mjs`)
  mkdirSync(dirname(outfile), { recursive: true })

  const result = await build({
    entryPoints: [join(HERE, "bin", `${name}.ts`)],
    outfile,
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node22.13",
    packages: "bundle",
    external: ["node:*"],
    sourcemap: false,
    legalComments: "none",
    banner: { js: BANNER },
    logLevel: quiet ? "silent" : "info",
  })

  // A `require(` call surviving into an ESM bundle means a dependency reached
  // for CommonJS at runtime, and the bundle would throw the first time that
  // line ran. It is a source problem, never something to paper over with a shim.
  for (const w of result.warnings) console.error(`[build] ${w.text}`)
  if (result.errors.length) process.exit(1)
}
