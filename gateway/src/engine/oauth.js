import { readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { randomBytes, createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import http from 'node:http';
import { proxyFetch } from './upstream-fetch.js';

/**
 * Import OAuth credentials from a Claude Code credentials file.
 */
export async function importCredentials(filePath) {
  const resolvedPath = filePath.replace(/^~/, homedir());
  const raw = JSON.parse(await readFile(resolvedPath, 'utf-8'));

  // Claude Code stores credentials nested under "claudeAiOauth"
  const data = raw.claudeAiOauth || raw;
  return {
    accessToken: data.accessToken,
    refreshToken: data.refreshToken,
    expiresAt: data.expiresAt,
    subscriptionType: data.subscriptionType,
    rateLimitTier: data.rateLimitTier,
  };
}

const PROFILE_URL = 'https://api.anthropic.com/api/oauth/profile';
const USAGE_URL = 'https://api.anthropic.com/api/oauth/usage';
const OAUTH_USAGE_BETA = 'oauth-2025-04-20';
const DEFAULT_TOKEN_ENDPOINT = 'https://platform.claude.com/v1/oauth/token';
const DEFAULT_CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e';

/**
 * Refresh an expired OAuth access token using the refresh token.
 * Retries on 5xx and network errors with exponential backoff.
 */
export async function refreshAccessToken(refreshToken, endpoint = DEFAULT_TOKEN_ENDPOINT) {
  const maxRetries = 2;
  const baseDelayMs = 500;
  // Bound each attempt so a dead pooled socket (after a network drop/reconnect)
  // can't hang the refresh forever. A hung refresh is especially harmful here:
  // ensureTokenFresh coalesces callers into a single _refreshPromise, so one
  // stuck refresh wedges every request for that account until a restart.
  const timeoutMs = Number(process.env.TEAMCLAUDE_REFRESH_TIMEOUT_MS) || 30_000;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      if (attempt > 0) {
        const delay = baseDelayMs * 2 ** (attempt - 1);
        await new Promise(resolve => setTimeout(resolve, delay));
      }

      const res = await proxyFetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json, text/plain, */*',
          'User-Agent': 'axios/1.13.6',
        },
        body: JSON.stringify({
          grant_type: 'refresh_token',
          refresh_token: refreshToken,
          client_id: DEFAULT_CLIENT_ID,
        }),
        signal: AbortSignal.timeout(timeoutMs),
      });

      if (!res.ok) {
        if (res.status >= 500 && attempt < maxRetries) {
          await res.body?.cancel();
          continue;
        }
        const text = await res.text();
        const err = new Error(`Token refresh failed (${res.status}): ${text}`);
        // Surface the HTTP status so callers can distinguish a genuine auth
        // rejection (the refresh token is dead — re-login needed) from a
        // transient server error. 5xx is retried above; reaching here with a 5xx
        // means retries were exhausted, which is still transient, not auth.
        err.status = res.status;
        throw err;
      }

      return tokenPairFromResponse(await res.json(), { previousRefreshToken: refreshToken });
    } catch (err) {
      const isNetworkError = err instanceof Error &&
        (err.name === 'TimeoutError' || err.name === 'AbortError' ||
          err.message.includes('fetch failed') ||
          (err.code === 'ECONNRESET' || err.code === 'ECONNREFUSED' ||
           err.code === 'ETIMEDOUT' || err.code === 'UND_ERR_CONNECT_TIMEOUT'));

      if (attempt < maxRetries && isNetworkError) {
        continue;
      }
      throw err;
    }
  }
}

/**
 * The credential fields of a token-endpoint response, checked.
 *
 * A 200 is not proof the body is usable. One without `access_token` used to be
 * stored as `accessToken: undefined` and sent upstream as `Bearer undefined`,
 * and a non-numeric expiry was stored as-is, where isTokenExpired never fired
 * on it. So a missing or empty access token is an error here, the refresh token
 * is taken only when it is a non-empty string (else the previous one is kept),
 * and the expiry is `expires_at` (seconds or milliseconds) or `expires_in`
 * seconds when either is a finite number — or one hour from now when neither
 * is, which just makes the next refresh happen early.
 */
export function tokenPairFromResponse(data, { previousRefreshToken = undefined, now = Date.now() } = {}) {
  const accessToken = data?.access_token;
  if (typeof accessToken !== 'string' || accessToken === '') {
    throw new Error('Token response carried no access_token');
  }
  const rotated = data.refresh_token;
  const refreshToken = typeof rotated === 'string' && rotated !== '' ? rotated : previousRefreshToken;
  const expiresIn = Number(data.expires_in);
  const expiresAt = normalizeExpiresAt(data.expires_at)
    ?? (Number.isFinite(expiresIn) && expiresIn > 0 ? now + expiresIn * 1000 : now + 3600 * 1000);
  return { accessToken, refreshToken, expiresAt };
}

/**
 * Normalize an expires_at value to milliseconds, or null when it is absent or
 * not a positive finite number — a value that cannot be compared with the clock
 * must not pass for one that can.
 * OAuth endpoints may return seconds; Claude Code credentials use milliseconds.
 */
export function normalizeExpiresAt(expiresAt) {
  if (!expiresAt) return null;
  const n = Number(expiresAt);
  if (!Number.isFinite(n) || n <= 0) return null;
  // If the value is plausibly in seconds (< 10^12 ≈ year 2001 in ms, year 33658 in s),
  // convert to milliseconds
  return n < 1e12 ? n * 1000 : n;
}

/**
 * Check if an OAuth token is expiring within the given threshold.
 *
 * No expiry at all means unknown, and the token is used until upstream says
 * otherwise. An expiry that is present but not a number is treated as already
 * reached: it cannot be trusted to lie in the future, and refreshing replaces
 * it with one that can be compared.
 */
export function isTokenExpiringSoon(expiresAt, thresholdMs = 5 * 60 * 1000) {
  if (!expiresAt) return false;
  const at = normalizeExpiresAt(expiresAt);
  if (at == null) return true;
  return Date.now() + thresholdMs >= at;
}

/**
 * Check if an OAuth token has ALREADY expired (no safety margin). Used to decide
 * when a token must be refreshed synchronously before it can be injected — a
 * still-valid-but-expiring-soon token is fine to use now and refresh in the
 * background, but an expired one would 401.
 */
export function isTokenExpired(expiresAt) {
  if (!expiresAt) return false;
  const at = normalizeExpiresAt(expiresAt);
  if (at == null) return true; // present but unusable — see isTokenExpiringSoon
  return Date.now() >= at;
}

/**
 * Fetch account profile for an OAuth token.
 * Returns { email, name, orgName, orgType, ... } on success,
 * or { error: 'reason' } on failure.
 */
export async function fetchProfile(accessToken) {
  try {
    const res = await proxyFetch(PROFILE_URL, {
      headers: { 'Authorization': `Bearer ${accessToken}` },
    });
    if (!res.ok) {
      let detail = '';
      try {
        const body = await res.json();
        detail = body?.error?.message || JSON.stringify(body).slice(0, 200);
      } catch {
        detail = await res.text().catch(() => '');
      }
      return { error: `HTTP ${res.status}${detail ? ': ' + detail : ''}` };
    }
    const data = await res.json();
    return {
      accountUuid: data.account?.uuid,
      email: data.account?.email,
      name: data.account?.display_name,
      orgUuid: data.organization?.uuid,
      orgName: data.organization?.name,
      orgType: data.organization?.organization_type,
      hasClaudeMax: data.account?.has_claude_max,
      hasClaudePro: data.account?.has_claude_pro,
    };
  } catch (err) {
    return { error: err.message || String(err) };
  }
}

// Pull a per-model weekly limit out of the payload's `limits[]` array, which is
// where the endpoint now reports model-scoped quota (a `weekly_scoped` entry
// carrying `scope.model.display_name`). Returns a bucket-shaped object
// { utilization, resets_at } ready for normalizeUsageBucket, or null if absent.
// The legacy top-level `seven_day_<model>` keys read null on current plans.
export function findScopedWeeklyLimit(data, modelNamePattern) {
  const limits = Array.isArray(data?.limits) ? data.limits : [];
  const entry = limits.find((l) =>
    l && l.group === 'weekly' && l.scope?.model?.display_name
    && modelNamePattern.test(l.scope.model.display_name));
  if (!entry) return null;
  return { utilization: entry.percent, resets_at: entry.resets_at };
}

// Normalize one usage bucket from the /api/oauth/usage payload into
// { utilization: 0-1, resetAt: ms-epoch }. The endpoint reports utilization
// as a percentage in the 0-100 range, so 1 means 1%, not 100%.
export function normalizeUsageBucket(bucket) {
  if (!bucket || typeof bucket !== 'object') return null;

  const rawPct = bucket.used_percentage ?? bucket.utilization ?? bucket.usedPercentage;
  const parsedPct = typeof rawPct === 'number' ? rawPct : parseFloat(rawPct);
  const utilization = Number.isFinite(parsedPct)
    ? parsedPct / 100
    : null;

  const rawReset = bucket.resets_at ?? bucket.resetsAt ?? bucket.reset_at ?? bucket.resetAt;
  let resetAt = null;
  if (typeof rawReset === 'number') {
    resetAt = rawReset < 1e12 ? rawReset * 1000 : rawReset;
  } else if (typeof rawReset === 'string') {
    const asNum = Number(rawReset);
    if (Number.isFinite(asNum) && rawReset.trim() !== '') {
      resetAt = asNum < 1e12 ? asNum * 1000 : asNum;
    } else {
      const parsed = Date.parse(rawReset);
      if (Number.isFinite(parsed)) resetAt = parsed;
    }
  }

  return { utilization, resetAt };
}

/**
 * Fetch OAuth subscription usage from the usage endpoint. This reports quota
 * utilization WITHOUT spending message quota, which is what makes it safe to
 * poll. Returns normalized { fiveHour, sevenDay, sevenDaySonnet, sevenDayFable } buckets, or
 * { error, status } on failure.
 */
export async function fetchUsage(accessToken) {
  try {
    const res = await proxyFetch(USAGE_URL, {
      headers: {
        'Authorization': `Bearer ${accessToken}`,
        'anthropic-beta': OAUTH_USAGE_BETA,
        'Accept': 'application/json',
      },
    });

    if (!res.ok) {
      let detail = '';
      try {
        const body = await res.json();
        detail = body?.error?.message || JSON.stringify(body).slice(0, 200);
      } catch {
        detail = await res.text().catch(() => '');
      }
      return { error: `HTTP ${res.status}${detail ? ': ' + detail : ''}`, status: res.status };
    }

    const data = await res.json();
    return {
      fiveHour: normalizeUsageBucket(data?.five_hour),
      sevenDay: normalizeUsageBucket(data?.seven_day),
      sevenDaySonnet: normalizeUsageBucket(data?.seven_day_sonnet),
      sevenDayFable: normalizeUsageBucket(findScopedWeeklyLimit(data, /fable/i)),
    };
  } catch (err) {
    return { error: err.message || String(err), status: null };
  }
}

// OAuth config (extracted from Claude Code). Client id + token endpoint are
// shared with the refresh path — see DEFAULT_CLIENT_ID / DEFAULT_TOKEN_ENDPOINT.
const OAUTH_AUTHORIZE = 'https://claude.ai/oauth/authorize';
const OAUTH_SCOPES = 'org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload';

/**
 * Perform OAuth login via browser with PKCE flow.
 * Opens the user's browser, waits for the callback, exchanges the code for tokens.
 */
export async function loginOAuth() {
  // Generate PKCE
  const codeVerifier = randomBytes(32).toString('base64url');
  const codeChallenge = createHash('sha256').update(codeVerifier).digest('base64url');
  const state = randomBytes(32).toString('base64url');

  // Start local callback server on a random port
  const { port, codePromise, server } = await startCallbackServer(state);
  const redirectUri = `http://localhost:${port}/callback`;

  // Build authorization URL
  const authUrl = new URL(OAUTH_AUTHORIZE);
  authUrl.searchParams.set('code', 'true');
  authUrl.searchParams.set('client_id', DEFAULT_CLIENT_ID);
  authUrl.searchParams.set('response_type', 'code');
  authUrl.searchParams.set('redirect_uri', redirectUri);
  authUrl.searchParams.set('scope', OAUTH_SCOPES);
  authUrl.searchParams.set('code_challenge', codeChallenge);
  authUrl.searchParams.set('code_challenge_method', 'S256');
  authUrl.searchParams.set('state', state);

  // Open browser
  console.log('Opening browser for authentication...');
  console.log(`If it doesn't open, visit:\n  ${authUrl.toString()}\n`);
  openBrowser(authUrl.toString());

  // Wait for either the callback server or manual paste from stdin
  let code;
  try {
    code = await raceWithStdinCode(codePromise, state);
  } finally {
    server.close();
  }

  // Exchange code for tokens
  console.log('Exchanging authorization code for tokens...');
  const tokenRes = await proxyFetch(DEFAULT_TOKEN_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      code,
      state,
      grant_type: 'authorization_code',
      client_id: DEFAULT_CLIENT_ID,
      redirect_uri: redirectUri,
      code_verifier: codeVerifier,
    }),
  });

  if (!tokenRes.ok) {
    const text = await tokenRes.text();
    throw new Error(`Token exchange failed (${tokenRes.status}): ${text}`);
  }

  return tokenPairFromResponse(await tokenRes.json());
}

/**
 * Race the callback server promise against manual code entry from stdin.
 * The user can paste the full callback URL or just the authorization code.
 */
function raceWithStdinCode(callbackPromise, expectedState) {
  if (!process.stdin.isTTY) return callbackPromise;

  return new Promise((resolve, reject) => {
    const rl = createInterface({ input: process.stdin, output: process.stderr });
    let settled = false;

    const settle = (fn, val) => {
      if (settled) return;
      settled = true;
      rl.close();
      fn(val);
    };

    rl.question('Paste authorization code here (or wait for browser callback): ', answer => {
      const trimmed = answer.trim();
      if (!trimmed) return; // empty input, keep waiting for callback

      // Try to parse as a URL with ?code= parameter
      try {
        const url = new URL(trimmed);
        const code = url.searchParams.get('code');
        const state = url.searchParams.get('state');
        if (code) {
          if (expectedState && state && state !== expectedState) {
            settle(reject, new Error('OAuth state mismatch'));
          } else {
            settle(resolve, code);
          }
          return;
        }
      } catch {}

      // Treat raw input as the authorization code
      settle(resolve, trimmed);
    });

    callbackPromise.then(
      code => settle(resolve, code),
      err => settle(reject, err),
    );
  });
}

function startCallbackServer(expectedState) {
  return new Promise((resolve, reject) => {
    let resolveCode, rejectCode;
    const codePromise = new Promise((res, rej) => { resolveCode = res; rejectCode = rej; });

    const server = http.createServer((req, res) => {
      const url = new URL(req.url, `http://localhost`);

      if (url.pathname === '/callback') {
        const code = url.searchParams.get('code');
        const error = url.searchParams.get('error');
        const state = url.searchParams.get('state');

        if (error) {
          res.writeHead(200, { 'Content-Type': 'text/html' });
          res.end('<html><body><h2>Authentication failed</h2><p>You can close this tab.</p></body></html>');
          rejectCode(new Error(`OAuth error: ${error} - ${url.searchParams.get('error_description') || ''}`));
          return;
        }

        if (expectedState && state !== expectedState) {
          res.writeHead(200, { 'Content-Type': 'text/html' });
          res.end('<html><body><h2>Authentication failed</h2><p>State mismatch. You can close this tab.</p></body></html>');
          rejectCode(new Error('OAuth state mismatch'));
          return;
        }

        if (code) {
          res.writeHead(302, { 'Location': 'https://platform.claude.com/oauth/code/success?app=claude-code' });
          res.end();
          resolveCode(code);
          return;
        }
      }

      res.writeHead(404);
      res.end('Not found');
    });

    server.listen(0, () => {
      resolve({ port: server.address().port, codePromise, server });
    });
    server.on('error', reject);

    // Timeout after 2 minutes (unref so it doesn't keep the process alive)
    const timer = setTimeout(() => {
      rejectCode(new Error('Login timed out after 2 minutes'));
      server.close();
    }, 120_000);
    timer.unref();
  });
}

/**
 * ak-gateway: the programs that can open `url` here, in the order tried — each as [command, args], no shell.
 * Windows: rundll32's FileProtocolHandler (`start "url"` would take the quoted URL for a window
 * title, and cmd.exe would split it at every `&`). Linux: wslview (WSL's bridge to the Windows
 * browser) before the freedesktop opener, which a headless box or a bare WSL distro may not have.
 */
export function browserCommands(url, platform = process.platform) {
  if (platform === 'darwin') return [['open', [url]]];
  if (platform === 'win32') return [['rundll32', ['url.dll,FileProtocolHandler', url]]];
  return [['wslview', [url]], ['xdg-open', [url]]]; // portable: ok — the Linux branch of a per-OS table
}

/** Best effort, never throws: the URL is always printed for the person to open by hand. */
function openBrowser(url) {
  const tryNext = (list) => {
    if (!list.length) return;
    const [cmd, args] = list[0];
    let child;
    try {
      child = spawn(cmd, args, { stdio: 'ignore', detached: true, windowsHide: true });
    } catch {
      return tryNext(list.slice(1));
    }
    let moved = false;
    const next = () => { if (!moved) { moved = true; tryNext(list.slice(1)); } };
    child.on('error', next);
    child.on('exit', (code) => { if (code !== 0 && code !== null) next(); });
    child.unref();
  };
  tryNext(browserCommands(url));
}
