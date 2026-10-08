#!/usr/bin/env bash
# find-node.sh — one node lookup, sourced by the POSIX shell doors `bin/traces` beside it and
# the app's terminal door `app/core/bin/brain-mcp`, which reaches it here. The Node entries
# (bin/traces.mjs, app/core/bin/brain-mcp.mjs) need none of it: whoever runs `node <entry>.mjs`
# already picked the interpreter, and the entry checks its version.
#
# Both ship as committed single-file ESM bundles (dist/traces.mjs, the app's
# dist/brain-mcp.mjs) that import `node:sqlite`, which is unflagged only from 22.13.
# The callers need the same answer and must never disagree: a launcher, which execs
# node, and the SessionStart install hook, which only warns. So the lookup lives once, here.
#
# NEITHER CALLER MAY WRITE TO STDOUT — the launcher's stdout is the MCP protocol
# channel and the hook's is injected session context. This file therefore never
# echoes anything: it sets globals and returns a status.
#
#   brain_find_node        → 0 and FIND_NODE = an interpreter ≥ $FIND_NODE_MIN
#                          → 1, with FIND_NODE/FIND_NODE_VERSION holding the best
#                            candidate found (empty when nothing resembling node exists)
#
# Sourced, not executed: no `set -e`, no exits, no side effects on the caller's shell
# beyond the three FIND_NODE* names.

FIND_NODE_MIN="22.13"

# Candidate interpreters, in the order the machine should be believed: what the
# session's PATH says, then every nvm version newest-first (nvm keeps each one it
# ever installed, and the `node` on PATH is often an old default), then the two
# places a package manager puts it.
brain_node_candidates() {
  command -v node 2>/dev/null || true
  ls -d "$HOME"/.nvm/versions/node/v*/bin/node 2>/dev/null | sort -V -r || true
  echo /opt/homebrew/bin/node # portable: ok — the POSIX shell door only; Homebrew on Apple Silicon
  echo /usr/local/bin/node
}

# 0 when $1 is a node ≥ FIND_NODE_MIN. FIND_NODE_VERSION is set either way, so a
# caller can name the version it rejected instead of saying "not found" about a
# node the user can plainly see.
brain_node_ok() {
  local v major minor
  FIND_NODE_VERSION=""
  v="$("$1" -p process.versions.node 2>/dev/null)" || return 1
  [ -n "$v" ] || return 1
  FIND_NODE_VERSION="$v"
  major="${v%%.*}"
  minor="${v#*.}"
  minor="${minor%%.*}"
  case "$major:$minor" in
    *[!0-9:]* | :* | *:) return 1 ;;
  esac
  if [ "$major" -gt 22 ]; then
    return 0
  fi
  if [ "$major" -eq 22 ] && [ "$minor" -ge 13 ]; then
    return 0
  fi
  return 1
}

brain_find_node() {
  local c best="" bestver="" list
  FIND_NODE=""
  FIND_NODE_VERSION=""
  list="$(brain_node_candidates)"
  # A here-string, not a pipe: a pipe would run the loop in a subshell and the
  # globals it sets would die with it.
  while IFS= read -r c; do
    [ -n "$c" ] || continue
    [ -x "$c" ] || continue
    if brain_node_ok "$c"; then
      FIND_NODE="$c"
      return 0
    fi
    if [ -z "$best" ] && [ -n "$FIND_NODE_VERSION" ]; then
      best="$c"
      bestver="$FIND_NODE_VERSION"
    fi
  done <<EOF
$list
EOF
  FIND_NODE="$best"
  FIND_NODE_VERSION="$bestver"
  return 1
}
