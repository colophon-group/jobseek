#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:-write}"

case "$mode" in
  write)
    exec uv run --project "$root/../.." python "$root/tools/generate_bindings.py"
    ;;
  --check)
    exec uv run --project "$root/../.." python "$root/tools/check_generated.py"
    ;;
  *)
    echo "usage: ./generate.sh [--check]" >&2
    exit 2
    ;;
esac
