/**
 * Settings that shadow the gateway.
 *
 * The gateway reaches a session through its launch environment: the routing
 * fragment (shim.ts) or the app's runner sets ANTHROPIC_BASE_URL and the rest
 * (claude-env.ts). Claude Code then applies every settings.json `env` block over
 * that environment — managed, then --settings, then the project's local and
 * shared files, then the user's — so a value there always wins (measured, and
 * docs/claude-code.md says where). Nothing here overrides anyone: it names what
 * is set where, and what that does to a session behind the gateway, so
 * `status` (and a setup that must not trample a user's choices) can say it.
 */
import { existsSync, readFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { claudeHome } from "./brand.ts"
import { CLAUDE_ENV } from "./claude-env.ts"

export type Scope = "managed" | "local" | "project" | "user" | "shell"

export type Shadow = { scope: Scope; file: string | null; name: string; value: string; effect: string }

/** A project's own settings folder, `<project>/.claude` — not Claude Code's config dir, which is claudeHome(). */
export const CLAUDE_DIR = ".claude" // portable: ok — a folder name inside a project, the same on every OS

/** Where an administrator's managed settings live, per OS. Windows has had two homes; both are read. */
export function managedSettings(platform: NodeJS.Platform = process.platform): string[] {
  if (platform === "darwin") return ["/Library/Application Support/ClaudeCode/managed-settings.json"] // portable: ok — the macOS location
  if (platform === "win32")
    return ["C:\\Program Files\\ClaudeCode\\managed-settings.json", "C:\\ProgramData\\ClaudeCode\\managed-settings.json"]
  return ["/etc/claude-code/managed-settings.json"]
}

/** The settings files Claude Code reads, highest precedence first. */
export function settingsFiles(opts: { home?: string; cwd?: string; configDir?: string } = {}): { scope: Scope; file: string }[] {
  const home = opts.home ?? homedir()
  const configDir = opts.configDir ?? (process.env.CLAUDE_CONFIG_DIR || !opts.home ? claudeHome() : join(home, CLAUDE_DIR))
  const cwd = opts.cwd ?? process.cwd()
  // The project is the directory claude starts in; walk up to the repository
  // root so a launch from a subfolder is covered too.
  const projects: string[] = []
  for (let d = cwd; ; d = dirname(d)) {
    if (d === home) break
    projects.push(d)
    if (existsSync(join(d, ".git")) || dirname(d) === d) break
  }
  return [
    ...managedSettings().map((file) => ({ scope: "managed" as const, file })),
    ...projects.map((d) => ({ scope: "local" as const, file: join(d, CLAUDE_DIR, "settings.local.json") })),
    ...projects.map((d) => ({ scope: "project" as const, file: join(d, CLAUDE_DIR, "settings.json") })),
    { scope: "user", file: join(configDir, "settings.json") },
  ]
}

function envOf(file: string): Record<string, string> {
  if (!existsSync(file)) return {}
  try {
    const env = (JSON.parse(readFileSync(file, "utf8")) as { env?: unknown }).env
    if (!env || typeof env !== "object") return {}
    return Object.fromEntries(Object.entries(env as Record<string, unknown>).map(([k, v]) => [k, String(v)]))
  } catch {
    return {}
  }
}

/** What a value set outside the routing does to a session behind the gateway, or null when it is harmless. */
export function effectOf(name: string, value: string, gatewayUrl: string, scope: Scope): string | null {
  const fixed = scope === "shell" ? "" : " — and it is fixed: no fall-back to Claude's own login when the gateway is down"
  switch (name) {
    case "ANTHROPIC_BASE_URL":
      if (value === "") return "empties the base URL: claude goes straight to Anthropic, never through the gateway"
      return value.startsWith(gatewayUrl)
        ? scope === "shell" ? null : `points at the gateway itself${fixed}`
        : `sends claude to ${value}, not through the gateway`
    case "ANTHROPIC_AUTH_TOKEN":
    case "ANTHROPIC_API_KEY":
    case "CLAUDE_CODE_OAUTH_TOKEN":
      return scope === "shell"
        ? null
        : "a credential the routing cannot unset; the gateway still replaces it with its own account, but a direct launch uses it"
    case "ANTHROPIC_CUSTOM_HEADERS":
      return "replaces the gateway's identity headers (x-brain-origin, x-brain-project): its trace cannot say where these sessions came from"
    case "ENABLE_TOOL_SEARCH":
      return value === CLAUDE_ENV.ENABLE_TOOL_SEARCH
        ? null
        : "keeps MCP tool search off behind the gateway: every tool's full definition goes up on every request (about half the prompt, measured)"
    case "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS":
      return value && value !== "0"
        ? "turns MCP tool search off whatever ENABLE_TOOL_SEARCH says, and strips other pre-release capabilities"
        : null
    case "CLAUDE_CODE_GATEWAY_HINT_HEADERS":
      return value === "0" ? "stops Claude Code's request hints: the trace cannot tell main turns from subagents or compactions" : null
    default:
      return null
  }
}

const WATCHED = [
  "ANTHROPIC_BASE_URL",
  "ANTHROPIC_AUTH_TOKEN",
  "ANTHROPIC_API_KEY",
  "CLAUDE_CODE_OAUTH_TOKEN",
  "ANTHROPIC_CUSTOM_HEADERS",
  "ENABLE_TOOL_SEARCH",
  "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
  "CLAUDE_CODE_GATEWAY_HINT_HEADERS",
]

/**
 * Every value, in any settings file or the shell `claude` starts from, that
 * changes what the gateway does for a session — highest precedence first.
 * Credentials are reported by name, never by value.
 */
export function shadows(gatewayUrl: string, opts: { home?: string; cwd?: string; configDir?: string; env?: Record<string, string | undefined> } = {}): Shadow[] {
  const out: Shadow[] = []
  const secret = (n: string) => /TOKEN|KEY/.test(n)
  for (const { scope, file } of settingsFiles(opts)) {
    const env = envOf(file)
    for (const name of WATCHED) {
      if (!(name in env)) continue
      const effect = effectOf(name, env[name], gatewayUrl, scope)
      if (effect) out.push({ scope, file, name, value: secret(name) ? "(set)" : env[name], effect })
    }
  }
  // The shell: the routing fills only what is unset, so an export here wins over it too.
  const shell = opts.env ?? process.env
  for (const name of Object.keys(CLAUDE_ENV)) {
    const v = shell[name]
    if (v === undefined || v === "") continue
    const effect = effectOf(name, v, gatewayUrl, "shell")
    if (effect) out.push({ scope: "shell", file: null, name, value: v, effect })
  }
  return out
}
