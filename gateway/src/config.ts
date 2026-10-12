/**
 * Gateway configuration.
 *
 * Hand-editable JSON at <configDir>/ak/gateway.json, every field optional.
 * Runtime state (trace, blobs, pid, log) lives under <dataDir>/ak/gateway so
 * the config file stays something a human reads. The dirs are the seam's
 * (brand.ts): $XDG_CONFIG_HOME or the home's .config, and $XDG_DATA_HOME or
 * .local/share, on macOS and Linux; %APPDATA% and %LOCALAPPDATA% on Windows.
 *
 * The gateway holds the credentials itself: the engine (engine.ts) serves and
 * refreshes the accounts in `accountsFile`, and nothing else on the machine
 * should refresh them — an OAuth refresh rotates the grant, and every other
 * holder's copy dies with `invalid_grant`.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { GATEWAY_ACCOUNTS, GATEWAY_CONFIG, GATEWAY_DATA, env as brandEnv, configDir, dataDir } from "./brand.ts"
import { privateDir } from "./fsperm.ts"

/** How much of a request body reaches disk (trace.ts `putCall`). Never headers, in any mode. */
export type CaptureLevel = "none" | "meta" | "gaps" | "full"

export type Config = {
  port: number
  host: string
  /** Where requests go, with an account's credential injected. */
  upstreamUrl: string
  /** The gateway's accounts file — tokens, refreshed here and nowhere else. */
  accountsFile: string
  /** Pin every request to one account by name/uuid when no route says otherwise. Empty = rotate. */
  account: string
  /** When every account is spent, hold the connection this long before a 429. */
  holdSeconds: number
  capture: CaptureLevel
  /** Days a stored body or tail is kept; rows and the small parts stay. 0 = forever. trace.ts `sweep`. */
  blobDays: number
  dataDir: string
  /** Policy rules, evaluated in order. See policy.ts. */
  policy: unknown[]
}

export const CONFIG_PATH =
  brandEnv("GATEWAY_CONFIG") ?? join(configDir(), GATEWAY_CONFIG)

const DEFAULTS: Config = {
  port: 4747,
  host: "127.0.0.1",
  upstreamUrl: "https://api.anthropic.com",
  accountsFile: join(configDir(), GATEWAY_ACCOUNTS),
  account: "",
  holdSeconds: 0,
  capture: "gaps",
  blobDays: 7,
  dataDir: join(dataDir(), GATEWAY_DATA),
  policy: [],
}

function readJson(path: string): Record<string, unknown> {
  if (!existsSync(path)) return {}
  try {
    const parsed: unknown = JSON.parse(readFileSync(path, "utf8"))
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {}
  } catch (err) {
    // A typo in the config must not silently fall back to defaults — that is how
    // you end up debugging a policy that was never loaded.
    throw new Error(`gateway: ${path} is not valid JSON — ${(err as Error).message}`)
  }
}

/** Env beats file beats default, so a probe can override one field inline. */
export function loadConfig(overrides: Partial<Config> = {}): Config {
  const file = readJson(CONFIG_PATH)
  const env: Partial<Config> = {}
  if (brandEnv("GATEWAY_PORT")) env.port = Number(brandEnv("GATEWAY_PORT"))
  if (brandEnv("GATEWAY_ACCOUNT")) env.account = brandEnv("GATEWAY_ACCOUNT")
  if (brandEnv("GATEWAY_ACCOUNTS_FILE")) env.accountsFile = brandEnv("GATEWAY_ACCOUNTS_FILE")
  if (brandEnv("GATEWAY_CAPTURE"))
    env.capture = brandEnv("GATEWAY_CAPTURE") as CaptureLevel
  if (brandEnv("GATEWAY_DATA_DIR")) env.dataDir = brandEnv("GATEWAY_DATA_DIR")

  const cfg: Config = { ...DEFAULTS, ...(file as Partial<Config>), ...env, ...overrides }

  // 0 is legal and means "ask the OS for a free one" — the tests and any
  // caller that wants an ephemeral wire depend on it.
  if (!Number.isInteger(cfg.port) || cfg.port < 0 || cfg.port > 65535)
    throw new Error(`gateway: port ${String(cfg.port)} is not a port`)
  if (!["none", "meta", "gaps", "full"].includes(cfg.capture))
    throw new Error(`gateway: capture must be none|meta|gaps|full, got ${String(cfg.capture)}`)
  if (!Number.isFinite(cfg.blobDays) || cfg.blobDays < 0)
    throw new Error(`gateway: blobDays must be a non-negative number, got ${String(cfg.blobDays)}`)
  if (!Number.isFinite(cfg.holdSeconds) || cfg.holdSeconds < 0)
    throw new Error(`gateway: holdSeconds must be a non-negative number, got ${String(cfg.holdSeconds)}`)

  return cfg
}

/**
 * Write a few fields into the config file, keeping everything else in it. The
 * file is hand-editable, so this is a merge and not a dump of the resolved
 * config — defaults stay implicit, only what changed is written down.
 */
export function writeConfigFields(patch: Partial<Config>): void {
  const file = readJson(CONFIG_PATH)
  const next = { ...file, ...patch }
  privateDir(dirname(CONFIG_PATH))
  writeFileSync(CONFIG_PATH, `${JSON.stringify(next, null, 2)}\n`, { mode: 0o600 })
}

export const paths = (cfg: Config) => ({
  trace: join(cfg.dataDir, "trace"),
  blobs: join(cfg.dataDir, "blobs"),
  xray: join(cfg.dataDir, "xray"),
  pins: join(cfg.dataDir, "pins"),
  pid: join(cfg.dataDir, "gateway.pid"),
  log: join(cfg.dataDir, "gateway.log"),
})

export function ensureDirs(cfg: Config): void {
  const p = paths(cfg)
  // Trace rows and blobs hold prompts. First, so on Windows what is made inside inherits the owner-only ACL.
  privateDir(cfg.dataDir)
  for (const d of [cfg.dataDir, p.trace, p.blobs, dirname(p.pid)])
    if (!existsSync(d)) mkdirSync(d, { recursive: true, mode: 0o700 })
}
