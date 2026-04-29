#!/usr/bin/env bash
# Purity gate for src/workspace/lib/.
#
# Lib modules must not import from CLI-only modules:
#   - src.workspace.commands.*
#   - src.workspace.cli
#   - src.workspace.output (the global "out" helper)
#
# Exits non-zero on any forbidden import, printing the offending lines.

set -euo pipefail

cd "$(dirname "$0")/.."

LIB_DIR="src/workspace/lib"
if [[ ! -d "$LIB_DIR" ]]; then
    echo "grep-lib-purity: $LIB_DIR not found" >&2
    exit 1
fi

# shellcheck disable=SC2086
violations=$(
    grep -RnE \
        -e '^[[:space:]]*from[[:space:]]+src\.workspace\.commands' \
        -e '^[[:space:]]*import[[:space:]]+src\.workspace\.commands' \
        -e '^[[:space:]]*from[[:space:]]+src\.workspace\.cli' \
        -e '^[[:space:]]*import[[:space:]]+src\.workspace\.cli' \
        -e '^[[:space:]]*from[[:space:]]+src\.workspace[[:space:]]+import[[:space:]]+output' \
        -e '^[[:space:]]*from[[:space:]]+src\.workspace\.output' \
        -e '^[[:space:]]*import[[:space:]]+src\.workspace\.output' \
        --include='*.py' \
        "$LIB_DIR" \
    || true
)

if [[ -n "$violations" ]]; then
    echo "grep-lib-purity: forbidden imports in $LIB_DIR:" >&2
    echo "$violations" >&2
    exit 1
fi

echo "grep-lib-purity: OK ($LIB_DIR has no forbidden imports)"
