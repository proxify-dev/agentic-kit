// Transparent claude shim — install / uninstall / status.
//
// Drops a tiny bash wrapper at <shim-dir>/claude, ships sh and fish PATH
// loaders alongside, and wires them in via a single source line in each
// detected shell rc (rustup-style — see ~/.cargo/env).
//
// The wrapper probes the proxy port and, if up, applies `teamclaude env`
// before exec'ing the real claude binary; otherwise it execs the real
// claude directly. The shim lives in its own directory, separate from
// where Claude Code's auto-updater rewrites its binary, so it survives
// `claude` self-updates indefinitely.
//
// Zero new dependencies — uses only Node.js built-in modules.

import { writeFileSync, readFileSync, existsSync, mkdirSync, rmSync, statSync, rmdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { homedir } from 'node:os';
import { loadOrCreateConfig } from './config.js';

// Comment that pairs with the source line so uninstall can strip both surgically.
const RC_COMMENT = '# teamclaude shim';

// ── embedded scripts ──────────────────────────────────────────

// The bash wrapper. `$var` references are bash; `\${...}` escapes are JS
// template-literal escapes for the same bash references.
const WRAPPER_SCRIPT = `#!/usr/bin/env bash
# claude shim — installed by \`teamclaude shim install\`.
# Routes \`claude\` through the teamclaude proxy when it's running, else direct.
#
# Lives in its own PATH-prepended directory so Claude Code's auto-updater
# (which rewrites the real claude binary on every update) cannot replace it.
#
# Resolution order for the real claude:
#   1. \$CLAUDE_REAL env override
#   2. first \`claude\` on PATH whose realpath != this script's realpath
set -e

realpath_of() {
  if readlink -f "\$1" >/dev/null 2>&1; then readlink -f "\$1"
  else python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "\$1"
  fi
}

SELF_REAL="\$(realpath_of "\$0")"

resolve_real_claude() {
  if [[ -n "\${CLAUDE_REAL:-}" && -x "\$CLAUDE_REAL" ]]; then
    printf '%s' "\$CLAUDE_REAL"; return 0
  fi
  local IFS=:
  for d in \$PATH; do
    local cand="\$d/claude"
    [[ -x "\$cand" ]] || continue
    local cr; cr="\$(realpath_of "\$cand")"
    [[ "\$cr" == "\$SELF_REAL" ]] && continue
    printf '%s' "\$cand"; return 0
  done
  return 1
}

# Read teamclaude proxy port from config (default 3456).
PORT=3456
CFG="\${TEAMCLAUDE_CONFIG:-\${XDG_CONFIG_HOME:-\$HOME/.config}/teamclaude.json}"
if [[ -r "\$CFG" ]]; then
  P=\$(awk -F'[ ,:]+' '/"port"[[:space:]]*:/ {print \$3; exit}' "\$CFG" 2>/dev/null || true)
  [[ "\$P" =~ ^[0-9]+\$ ]] && PORT="\$P"
fi

proxy_up() {
  if command -v nc >/dev/null 2>&1; then
    nc -z -w 1 127.0.0.1 "\$PORT" >/dev/null 2>&1
  else
    (exec 3<>/dev/tcp/127.0.0.1/"\$PORT") >/dev/null 2>&1
  fi
}

if ! REAL="\$(resolve_real_claude)"; then
  echo "claude-shim: cannot find real claude binary on PATH" >&2
  echo "  set CLAUDE_REAL=/path/to/claude or install Claude Code." >&2
  exit 127
fi

# Proxy api key, baked at install time. Re-run \`teamclaude shim install\`
# if you change \`proxy.apiKey\` in your teamclaude config.
#
# Why baked vs. \`eval "\$(teamclaude env)"\`: the previous design needed the
# \`teamclaude\` binary on PATH, which only happens in interactive shells
# (nvm/npm shims aren't loaded by cron, launchd, IDE-spawned subshells, or
# Python SDK subprocesses). Baking removes that dependency entirely.
APIKEY='__TEAMCLAUDE_API_KEY__'

if proxy_up; then
  if [[ -n "\$APIKEY" ]]; then
    export ANTHROPIC_BASE_URL="http://localhost:\$PORT"
    export ANTHROPIC_API_KEY="\$APIKEY"
  else
    echo "claude-shim: proxy up on :\$PORT but no apiKey baked into the shim —" >&2
    echo "  re-run 'teamclaude shim install' to refresh. Falling through to" >&2
    echo "  direct auth (will use whatever credentials claude finds locally)." >&2
  fi
fi

exec "\$REAL" "\$@"
`;

// POSIX sh loader — rustup-style. Idempotent at source time.
//
// Two responsibilities:
//   (1) PATH-prepend the shim dir, so interactive `claude` invocations route
//       through the bash wrapper.
//   (2) Export ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY into the shell, so any
//       subprocess (Python SDK, claude-agent-sdk's bundled binary, curl,
//       direct Anthropic-SDK calls, …) inherits proxy routing.
//
// (2) is non-optional: claude-agent-sdk ships its own _bundled/claude
// and prefers it over PATH lookup, so the shim wrapper from (1) is bypassed
// on every SDK call. Without env-level routing the SDK calls api.anthropic.com
// directly.
//
// The exports are gated by `[ -z "$VAR" ]` so users can override at the
// shell or per-process level (e.g. to point at a real ANTHROPIC_API_KEY).
function envShScript(shimDirRef, baseUrl, apiKey) {
  return `#!/bin/sh
# teamclaude-shim shell setup — sourced from your shell rc.
# Idempotent: safe to source multiple times.

# (1) PATH: route plain \`claude\` invocations through the shim wrapper.
case ":\${PATH}:" in
    *:"${shimDirRef}":*)
        ;;
    *)
        export PATH="${shimDirRef}:$PATH"
        ;;
esac

# (2) Env: route SDK / Anthropic-SDK / curl traffic through the proxy.
# Baked at install time. Re-run \`teamclaude shim install\` if your config
# (proxy.port / proxy.apiKey) changes. Only sets if not already set, so
# per-shell overrides keep working.
if [ -z "\${ANTHROPIC_BASE_URL:-}" ]; then
    export ANTHROPIC_BASE_URL='${baseUrl}'
fi
if [ -z "\${ANTHROPIC_API_KEY:-}" ]; then
    export ANTHROPIC_API_KEY='${apiKey}'
fi
`;
}

// Fish loader — separate file because fish syntax differs.
function envFishScript(shimDirRef, baseUrl, apiKey) {
  return `# teamclaude-shim fish setup — auto-loaded from conf.d.

# (1) PATH: route plain \`claude\` through the shim wrapper.
if not contains "${shimDirRef}" $PATH
    set -gx PATH "${shimDirRef}" $PATH
end

# (2) Env: route SDK / direct API traffic through the proxy.
if not set -q ANTHROPIC_BASE_URL
    set -gx ANTHROPIC_BASE_URL '${baseUrl}'
end
if not set -q ANTHROPIC_API_KEY
    set -gx ANTHROPIC_API_KEY '${apiKey}'
end
`;
}

// ── path helpers ──────────────────────────────────────────────

export function defaultShimDir() {
  const data = process.env.XDG_DATA_HOME || join(homedir(), '.local', 'share');
  return join(data, 'teamclaude-shim');
}

// Render an absolute path as `$HOME/...` when it sits under $HOME, else
// return it absolute. Matches rustup's ~/.cargo/env style — portable
// across machines with the same shape of home dir.
function homeRef(absPath) {
  const home = homedir();
  if (absPath === home) return '$HOME';
  if (absPath.startsWith(home + '/')) return '$HOME/' + absPath.slice(home.length + 1);
  return absPath;
}

// All sh-family rc files we'll attempt to wire up. Writing to multiple
// rc files (rather than picking one) is what makes rustup's pattern
// robust to macOS bash login-shell precedence and cross-distro differences.
//
// Note on zsh: PATH belongs in `.zshenv`, NOT `.zshrc`. `.zshrc` runs only
// for interactive shells, so non-interactive subshells (Python SDKs spawning
// `claude`, IDE Run buttons, cron, launchd, `uv run`, etc.) would miss the
// shim entirely and silently route to direct OAuth.
function shFamilyRcs() {
  const home = homedir();
  return [
    join(home, '.profile'),       // POSIX login shell baseline
    join(home, '.bashrc'),        // bash interactive non-login
    join(home, '.bash_profile'),  // bash login (macOS Terminal default)
    join(home, '.zshenv'),        // zsh — sourced for ALL invocations
  ];
}

// Files we used to write to but no longer manage. Still scrubbed on
// install/uninstall so users upgrading from older versions get migrated
// transparently and don't end up with the shim path duplicated.
function legacyRcs() {
  return [
    join(homedir(), '.zshrc'),    // pre-fix: PATH was wired here (interactive only)
  ];
}

function fishConfDirPath() {
  const cfg = process.env.XDG_CONFIG_HOME || join(homedir(), '.config');
  return join(cfg, 'fish', 'conf.d');
}

function fishConfFilePath() {
  return join(fishConfDirPath(), 'teamclaude-shim.fish');
}

// ── install / uninstall / status ──────────────────────────────

export async function install({ shimDir = defaultShimDir(), noRc = false } = {}) {
  mkdirSync(shimDir, { recursive: true });

  const wrapperPath = join(shimDir, 'claude');
  const envShPath = join(shimDir, 'env');
  const envFishPath = join(shimDir, 'env.fish');
  const shimDirRef = homeRef(shimDir);

  // Bake the proxy creds at install time. Doing it here (instead of at
  // runtime via `teamclaude env`) keeps the shim free of any dependency
  // on `teamclaude`/`node` being on PATH — those are absent in cron,
  // launchd, IDE-spawned subshells, and SDK subprocesses.
  const config = await loadOrCreateConfig();
  const apiKey = config?.proxy?.apiKey || '';
  const port = config?.proxy?.port || 3456;
  const baseUrl = `http://localhost:${port}`;
  if (!apiKey) {
    console.warn('warning: proxy.apiKey missing from config; the shim will warn at runtime.');
  }
  const wrapperContent = WRAPPER_SCRIPT.replace('__TEAMCLAUDE_API_KEY__', apiKey);

  writeFileSync(wrapperPath, wrapperContent, { mode: 0o755 });
  writeFileSync(envShPath, envShScript(shimDirRef, baseUrl, apiKey), { mode: 0o644 });
  writeFileSync(envFishPath, envFishScript(shimDirRef, baseUrl, apiKey), { mode: 0o644 });
  console.log(`Wrote shim:    ${wrapperPath}`);
  console.log(`Wrote loader:  ${envShPath}`);
  console.log(`Wrote loader:  ${envFishPath}`);

  if (noRc) {
    console.log('');
    console.log('Add this to your shell rc and reload:');
    console.log(`  . "${homeRef(envShPath)}"          # bash / zsh / sh`);
    console.log(`  source "${homeRef(envFishPath)}"   # fish`);
    return { wrapperPath, envShPath, envFishPath };
  }

  // Migrate legacy locations (e.g. .zshrc from before we knew PATH belongs
  // in .zshenv). Strip silently — leaving stale entries causes duplicate
  // PATH prepends, which is harmless but noisy.
  for (const rc of legacyRcs()) {
    if (!existsSync(rc)) continue;
    if (stripSourceLine(rc, envShPath)) {
      console.log(`Migrated rc:   removed legacy shim line from ${rc}`);
    }
  }

  // sh-family: append a one-line source directive to each rc file we recognize.
  // Skip files that don't exist AND don't correspond to the user's $SHELL —
  // creating a .bashrc on a zsh-only system is rude.
  let touched = 0;
  for (const rc of shFamilyRcs()) {
    if (!shouldTouchRc(rc)) continue;
    if (appendSourceLine(rc, envShPath)) {
      console.log(`Updated rc:    ${rc}`);
      touched++;
    }
  }

  // fish: drop a file in conf.d (fish auto-loads everything in that dir).
  // No rc edit needed — this is the canonical fish convention.
  const fishConfPath = fishConfFilePath();
  mkdirSync(dirname(fishConfPath), { recursive: true });
  writeFileSync(fishConfPath, fishConfContent(envFishPath));
  console.log(`Wrote fish:    ${fishConfPath}`);

  console.log('');
  if (touched > 0) {
    console.log('Reload your shell (or open a new terminal) to pick up the change.');
  } else {
    console.log('No sh-family rc files were modified. Add this to your rc manually:');
    console.log(`  . "${homeRef(envShPath)}"`);
  }
  console.log('');
  console.log('Verify:');
  console.log(`  which claude     # should print ${wrapperPath}`);

  return { wrapperPath, envShPath, envFishPath };
}

export function uninstall({ shimDir = defaultShimDir() } = {}) {
  const wrapperPath = join(shimDir, 'claude');
  const envShPath = join(shimDir, 'env');
  const envFishPath = join(shimDir, 'env.fish');

  for (const f of [wrapperPath, envShPath, envFishPath]) {
    if (existsSync(f)) {
      rmSync(f);
      console.log(`Removed:       ${f}`);
    }
  }
  try { rmdirSync(shimDir); } catch { /* not empty or doesn't exist */ }

  // sh-family: strip our source line from current AND legacy locations.
  for (const rc of [...shFamilyRcs(), ...legacyRcs()]) {
    if (!existsSync(rc)) continue;
    if (stripSourceLine(rc, envShPath)) {
      console.log(`Cleaned rc:    ${rc}`);
    }
  }

  // fish: remove the conf.d drop-file.
  const fishConfPath = fishConfFilePath();
  if (existsSync(fishConfPath)) {
    rmSync(fishConfPath);
    console.log(`Removed fish:  ${fishConfPath}`);
  }

  console.log('');
  console.log('Reload your shell to drop the PATH entry.');
}

export function status({ shimDir = defaultShimDir() } = {}) {
  const wrapperPath = join(shimDir, 'claude');
  const envShPath = join(shimDir, 'env');
  const envFishPath = join(shimDir, 'env.fish');

  console.log(`Shim dir:      ${shimDir}`);
  console.log(`Wrapper:       ${existsSync(wrapperPath) ? `installed (mode ${(statSync(wrapperPath).mode & 0o777).toString(8)})` : 'missing'}`);
  console.log(`sh loader:     ${existsSync(envShPath) ? 'installed' : 'missing'}`);
  console.log(`fish loader:   ${existsSync(envFishPath) ? 'installed' : 'missing'}`);

  const pathDirs = (process.env.PATH || '').split(':');
  const onPath = pathDirs.includes(shimDir);
  console.log(`On PATH:       ${onPath ? 'yes' : 'no'}`);

  console.log('');
  console.log('Wired into:');
  let any = false;
  for (const rc of [...shFamilyRcs(), ...legacyRcs()]) {
    if (!existsSync(rc)) continue;
    const text = readFileSync(rc, 'utf8');
    if (text.includes(`. "${homeRef(envShPath)}"`) || text.includes(`. "${envShPath}"`)) {
      const tag = legacyRcs().includes(rc) ? '  (legacy — run `teamclaude shim install` to migrate)' : '';
      console.log(`  ${rc}${tag}`);
      any = true;
    }
  }
  const fishConfPath = fishConfFilePath();
  if (existsSync(fishConfPath)) {
    console.log(`  ${fishConfPath}`);
    any = true;
  }
  if (!any) console.log('  (nothing — run `teamclaude shim install`)');
}

// ── rc-edit primitives ────────────────────────────────────────

// Should we add a source line to this rc file? Yes if:
//   1. The file already exists (user uses this shell), OR
//   2. The file matches the user's current $SHELL (so first install creates it).
// This avoids creating .bashrc on a zsh-only machine.
function shouldTouchRc(rcPath) {
  if (existsSync(rcPath)) return true;
  const shell = (process.env.SHELL || '').split('/').pop();
  const base = rcPath.split('/').pop();
  if (shell === 'zsh' && base === '.zshenv') return true;
  if (shell === 'bash' && (base === '.bashrc' || base === '.bash_profile')) return true;
  return false;
}

function sourceLineFor(envShPath) {
  return `. "${homeRef(envShPath)}"`;
}

function appendSourceLine(rcPath, envShPath) {
  const line = sourceLineFor(envShPath);
  const altLine = `. "${envShPath}"`; // legacy/absolute form for detection
  let text = '';
  try { text = readFileSync(rcPath, 'utf8'); } catch { /* file may not exist */ }

  if (text.includes(line) || text.includes(altLine)) return false; // already wired

  const block = `\n${RC_COMMENT}\n${line}\n`;
  if (text && !text.endsWith('\n')) text += '\n';
  text += block;
  writeFileSync(rcPath, text);
  return true;
}

function stripSourceLine(rcPath, envShPath) {
  const text = readFileSync(rcPath, 'utf8');
  const line = sourceLineFor(envShPath);
  const altLine = `. "${envShPath}"`;
  if (!text.includes(line) && !text.includes(altLine)) return false;

  // Match: optional leading newline, our comment line, the source line,
  // optional trailing newline. Keep the rest intact.
  const re = new RegExp(
    `\\n?${escapeRe(RC_COMMENT)}\\n(?:${escapeRe(line)}|${escapeRe(altLine)})\\n?`,
    'g'
  );
  let cleaned = text.replace(re, '\n');
  // Belt-and-suspenders: also strip a bare source line not preceded by our comment.
  const bareRe = new RegExp(`\\n?(?:${escapeRe(line)}|${escapeRe(altLine)})\\n?`, 'g');
  cleaned = cleaned.replace(bareRe, '\n');
  cleaned = cleaned.replace(/\n{3,}/g, '\n\n');
  writeFileSync(rcPath, cleaned);
  return true;
}

function fishConfContent(envFishPath) {
  return `${RC_COMMENT}\nsource "${homeRef(envFishPath)}"\n`;
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
