#!/bin/sh
# Copyright 2026 Phil Pennock — see LICENSE file.
#
# Run the official MCP conformance suite's client scenarios against this
# driver (in --conformance-client mode), for each revision's frozen list of
# required scenarios in docs/specs/conformance/<rev>.yaml.
#
# Usage: scripts/conformance-client.sh [revision ...]   (default: all vendored)
# Needs node/npx.  The runner version is pinned to the release the
# 2026-07-28 requirements were frozen against.

set -u

RUNNER="${CONFORMANCE_RUNNER:-@modelcontextprotocol/conformance@0.2.0-alpha.10}"
top="$(git rev-parse --show-toplevel)"
driver="${MCP_TEST_DRIVER:-$top/.venv/bin/mcp-test-driver}"
[ -x "$driver" ] || { echo "$0: $driver not found; run 'uv sync' first" >&2; exit 2; }

if [ $# -eq 0 ]; then
  set -- $(cd "$top/docs/specs/conformance" && ls *.yaml | sed 's/\.yaml$//')
fi

# The scenarios listed under the top-level "client:" key of a requirements file.
client_scenarios() {
  awk '/^client:/ {in_c = 1; next} /^[a-z_]+:/ {in_c = 0} in_c && /^  - / {print $2}' "$1"
}

pass=0
fail=0
for rev in "$@"; do
  for scenario in $(client_scenarios "$top/docs/specs/conformance/$rev.yaml"); do
    out=$(npx -y "$RUNNER" client --command "$driver --conformance-client" \
            --scenario "$scenario" --spec-version "$rev" 2>&1)
    if printf '%s' "$out" | grep -q "OVERALL: PASSED"; then
      pass=$((pass + 1))
      printf 'pass  %s %s\n' "$rev" "$scenario"
    elif printf '%s' "$out" | grep -q "SKIPPED"; then
      printf 'skip  %s %s\n' "$rev" "$scenario"
    else
      fail=$((fail + 1))
      printf 'FAIL  %s %s\n' "$rev" "$scenario"
      printf '%s\n' "$out" | grep -E "FAILURE|WARNING|Error" | sed 's/^/        /' | head -10
    fi
  done
done
printf '%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
