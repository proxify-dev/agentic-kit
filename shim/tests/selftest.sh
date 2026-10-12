#!/bin/sh
# The shim's tests are Node now (selftest.test.mjs, `node --test`). This runs them for callers
# that still say `bash tests/selftest.sh` — scripts/brand.py's canary counts its `ok`/`FAIL`
# lines — printing one line per test and exiting with node's code.
cd "$(dirname "$0")" || exit 1
out=$(node --test --test-reporter=tap selftest.test.mjs 2>&1)
code=$?
printf '%s\n' "$out" | sed -n -e 's/^ *ok [0-9]* - /ok   /p' -e 's/^ *not ok [0-9]* - /FAIL /p'
[ "$code" = 0 ] && echo "ALL PASS" || echo "FAILURES PRESENT"
exit $code
