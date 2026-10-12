/**
 * What a Claude Code session needs, beyond the base URL, to behave behind this
 * gateway as it does against api.anthropic.com. Claude Code treats any other base
 * URL as a proxy it cannot trust with its newer request shapes, and quietly turns
 * those off; these turn back on the ones this gateway is known to carry.
 *
 * Where they are set, and why only there:
 *
 *   - alongside ANTHROPIC_BASE_URL, by the routing fragment (shim.ts) and the
 *     app's runner — so they ride with the gateway's address. A launch the
 *     gateway does not route (it is down, or `disable`d) gets neither, and
 *     Claude Code's own defaults apply. A value like `ENABLE_TOOL_SEARCH=true`
 *     would break a session aimed at a proxy that does NOT forward
 *     `tool_reference` blocks, so it never goes anywhere global.
 *   - never in anyone's settings.json. A value the user set wins: an exported
 *     shell variable because the fragment only fills an unset one, and a
 *     settings.json `env` entry because Claude Code applies those over the
 *     launch environment (measured: docs/claude-code.md, "Who wins").
 */
import { basename } from "node:path"

export const CLAUDE_ENV = {
  /**
   * MCP tool search: tools go up as names until Claude searches for one. Off by
   * default behind a non-first-party base URL; this gateway forwards
   * `tool_reference` blocks and the beta header untouched (the engine passes
   * `anthropic-*` headers and body fields as open lists). Measured on one
   * heavily-tooled session: ~116K → ~57K prompt tokens per request.
   */
  ENABLE_TOOL_SEARCH: "true",
  /**
   * Per-request routing hints (`x-claude-code-*`): fixed vocabularies, tool
   * names and durations, never prompt text. Sent to api.anthropic.com by
   * default, to a custom base URL only when asked. The trace records them.
   */
  CLAUDE_CODE_GATEWAY_HINT_HEADERS: "1",
} as const

export type ClaudeEnvName = keyof typeof CLAUDE_ENV

/** The entries of CLAUDE_ENV the given environment does not already set. */
export function claudeEnvDefaults(env: Record<string, string | undefined>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(CLAUDE_ENV)) if (env[k] === undefined || env[k] === "") out[k] = v
  return out
}

/**
 * What a program needs for its model calls to go through the gateway at `base`:
 * the base URL, the doorman pass (a placeholder — the gateway injects the real
 * token) and CLAUDE_ENV's entries the given environment does not set. No
 * identity headers: a caller adds its own. `routedEnv` and `env --json` both
 * start here.
 */
export function routing(base: string, from: Record<string, string | undefined> = process.env): Record<string, string> {
  return { ANTHROPIC_BASE_URL: base, ANTHROPIC_AUTH_TOKEN: "gateway-doorman", ...claudeEnvDefaults(from) }
}

/**
 * The environment of a claude one of our programs launches onto a gateway —
 * `try`, `probe`, `ak xray` — as the routing fragment (shim.ts) gives a
 * terminal launch: the gateway as the base URL, the doorman pass, no inherited
 * credential (it would outrank the base URL), CLAUDE_ENV's unset entries, and
 * the caller's `x-brain-*` headers in place of any it inherited; other custom
 * headers are kept. `CC_SHIM_DISABLE=1`: `claude` on PATH is the cc-shim, whose
 * appended system prompt still applies, while its routing fragment would
 * re-aim the base URL and add headers of its own.
 */
export function routedEnv(base: string, headers: string[], from: Record<string, string | undefined> = process.env): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...from }
  delete env.CLAUDE_CODE_OAUTH_TOKEN
  delete env.ANTHROPIC_API_KEY
  env.CC_SHIM_DISABLE = "1"
  Object.assign(env, routing(base, env))
  const kept = (env.ANTHROPIC_CUSTOM_HEADERS ?? "").split("\n").filter((l) => l.trim() && !/^x-brain-/i.test(l.trim()))
  env.ANTHROPIC_CUSTOM_HEADERS = [...kept, ...headers].join("\n")
  return env
}

/**
 * The environment of a terminal claude routed without the cc-shim — `run`
 * launches with it and `env` prints it, so the two cannot drift. What the
 * routing fragment (shim.ts) gives a launch: routedEnv, the identity a terminal
 * launch carries (origin `cli`, project = the folder's name), and an account
 * pin as the gateway's `/tc-acct/<name>` path prefix. `account` is a name the
 * gateway already resolved (/_gateway/resolve), never a partial one.
 */
export function terminalEnv(
  gateway: string,
  opts: { account?: string | null; cwd?: string } = {},
  from: Record<string, string | undefined> = process.env,
): NodeJS.ProcessEnv {
  const base = opts.account ? `${gateway}/tc-acct/${opts.account}` : gateway
  return routedEnv(base, ["x-brain-origin: cli", `x-brain-project: ${basename(opts.cwd ?? process.cwd())}`], from)
}
