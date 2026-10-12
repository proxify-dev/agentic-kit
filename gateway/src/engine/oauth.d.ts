// Declarations for the vendored oauth.js — see types.d.ts.
import type { Tokens } from "./types.d.ts"

export function loginOAuth(): Promise<Tokens>
export function importCredentials(path: string): Promise<Tokens & { subscriptionType?: string; rateLimitTier?: string }>
export function fetchProfile(accessToken: string): Promise<{
  error?: string
  accountUuid?: string
  email?: string
  name?: string
  orgUuid?: string
  orgName?: string
  hasClaudeMax?: boolean
  hasClaudePro?: boolean
} | null>
export function refreshAccessToken(refreshToken: string): Promise<Tokens>
export type UsageBucket = { utilization: number | null; resetAt: number | null } | null
export function fetchUsage(accessToken: string): Promise<
  | { fiveHour: UsageBucket; sevenDay: UsageBucket; sevenDaySonnet: UsageBucket; sevenDayFable: UsageBucket; error?: undefined }
  | { error: string; status: number | null }
>
/** The programs that can open a URL on this OS, in the order tried: [command, args], no shell. */
export function browserCommands(url: string, platform?: NodeJS.Platform): [string, string[]][]
