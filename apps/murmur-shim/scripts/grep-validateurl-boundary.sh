#!/usr/bin/env bash
# grep-validateurl-boundary.sh
#
# Asserts the caller boundary for `validateUrl` (and `safeFetch`) from
# `apps/murmur-shim/src/lib/murmur/ssrf.ts`: those names must only be
# imported from
#   - apps/murmur-shim/app/api/**/route.ts                       (route handlers)
#   - apps/murmur-shim/app/api/murmur/_lib/handle.ts             (J5 shared route
#                                                                 dispatcher — IS
#                                                                 the boundary,
#                                                                 called by all
#                                                                 seven Murmur
#                                                                 routes)
#   - apps/murmur-shim/src/lib/murmur/ssrf.ts                    (defining module)
#   - apps/murmur-shim/src/lib/murmur/ssrf.test.ts               (its own test)
#
# Anywhere else is a boundary violation: SSRF validation must happen at
# the request boundary so the structured `url_*` error code can be
# surfaced to the agent in the response envelope. Pushing it deeper
# means individual call sites won't be checked.
#
# Usage:  bash apps/murmur-shim/scripts/grep-validateurl-boundary.sh
# Exit:   0 if clean, 1 if any unauthorised import is found.
#
# @see colophon-group/jobseek#2758
# @see colophon-group/jobseek#2773 (carve-out: scan path moved web -> murmur-shim)

set -euo pipefail

# Resolve repo root from this script's location so the gate works the
# same whether invoked from CI, lefthook, or `pnpm grep:*`.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHIM_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$SHIM_ROOT/../.." && pwd)"

cd "$REPO_ROOT"

# Files that import `validateUrl` or `safeFetch` from the ssrf module.
# Two patterns: relative `./ssrf` or `@/lib/murmur/ssrf`.
matches=$(
  grep -rEn \
    --include='*.ts' --include='*.tsx' \
    "from ['\"](.*lib/murmur/ssrf|\\./ssrf|\\.\\./ssrf|@/lib/murmur/ssrf)['\"]" \
    apps/murmur-shim 2>/dev/null || true
)

violations=""
while IFS= read -r line; do
  [ -z "$line" ] && continue
  file="${line%%:*}"
  case "$file" in
    apps/murmur-shim/app/api/*/route.ts) ;;
    apps/murmur-shim/app/api/*/*/route.ts) ;;
    apps/murmur-shim/app/api/*/*/*/route.ts) ;;
    apps/murmur-shim/app/api/*/*/*/*/route.ts) ;;
    apps/murmur-shim/app/api/murmur/_lib/handle.ts) ;;
    apps/murmur-shim/src/lib/murmur/ssrf.ts) ;;
    apps/murmur-shim/src/lib/murmur/ssrf.test.ts) ;;
    *)
      violations+="$line"$'\n'
      ;;
  esac
done <<< "$matches"

if [ -n "$violations" ]; then
  echo "validateUrl/safeFetch must only be imported at route boundaries." >&2
  echo "Offending imports:" >&2
  printf '%s' "$violations" >&2
  exit 1
fi

echo "grep-validateurl-boundary: clean."
