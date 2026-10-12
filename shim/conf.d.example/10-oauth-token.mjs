// 10-oauth-token.mjs — EXAMPLE. Copy to your cc-shim conf.d to activate
// (~/.config/cc-shim/conf.d, %APPDATA%\cc-shim\conf.d on Windows).
//
// Routes this one `claude` invocation at a specific OAuth account.
// CLAUDE_CODE_OAUTH_TOKEN short-circuits Claude Code's entire credential stack,
// so nothing else has to change. The child cannot refresh, so whatever hands
// over the token must give it one with life left.
//
// Contract reminders (docs/reference.md, "The conf.d contract"):
//   * runs on EVERY claude invocation, including `claude --version` and every
//     SDK subprocess — be fast and side-effect free
//   * it runs in a worker thread: set process.env, and that is what is imported;
//     process.exit() or a throw means nothing you set is imported
//   * any network call needs its own timeout (AbortSignal.timeout(2000))
//   * CC_SHIM_CLAIM stops later fragments; CC_SHIM_UNSET removes inherited vars
import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

let tok = ""
try {
  tok = readFileSync(join(homedir(), ".config", "cc-shim", "token"), "utf8").trim() // portable: ok — an example path
} catch {
  process.exit(0)
}
if (!tok) process.exit(0)

process.env.CLAUDE_CODE_OAUTH_TOKEN = tok

// Stale exports from a previous proxy setup would otherwise win over the token.
process.env.CC_SHIM_UNSET = "ANTHROPIC_API_KEY ANTHROPIC_BASE_URL"
process.env.CC_SHIM_CLAIM = "oauth-token"
