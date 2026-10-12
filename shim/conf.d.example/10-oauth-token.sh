# 10-oauth-token.sh — EXAMPLE. Copy to ~/.config/cc-shim/conf.d/ to activate.
#
# Routes this one `claude` invocation at a specific OAuth account.
# CLAUDE_CODE_OAUTH_TOKEN short-circuits Claude Code's entire credential stack
# (its getter returns {accessToken: <env>, refreshToken: null} before touching
# the keychain), so nothing else has to change. Note the child cannot refresh,
# so whatever hands over the token must give it one with life left.
#
# Contract reminders:
#   * runs on EVERY claude invocation, including `claude --version` and every
#     SDK subprocess — be fast and side-effect free
#   * any network call needs its own timeout (`curl --max-time 2`); the shim's
#     5 s backstop is a last resort, not a budget
#   * a .sh fragment runs on macOS and Linux only; 10-oauth-token.mjs is the
#     same fragment for every OS
#   * CC_SHIM_CLAIM stops later fragments; CC_SHIM_UNSET removes inherited vars
#   * assignment is enough — the shim force-exports both contract variables

tok=$(cat "$HOME/.config/cc-shim/token" 2>/dev/null) || exit 0
[ -n "$tok" ] || exit 0

export CLAUDE_CODE_OAUTH_TOKEN="$tok"

# Stale exports from a previous proxy setup would otherwise win over the token.
CC_SHIM_UNSET="ANTHROPIC_API_KEY ANTHROPIC_BASE_URL"
CC_SHIM_CLAIM=oauth-token
