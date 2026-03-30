#!/usr/bin/env bash
#
# Test crawler memory usage inside Docker with a hard memory limit.
#
# Usage:
#   ./scripts/docker_memory_test.sh 256m          # test with 256 MB
#   ./scripts/docker_memory_test.sh 512m           # test with 512 MB
#   ./scripts/docker_memory_test.sh 1g             # test with 1 GB
#   ./scripts/docker_memory_test.sh 512m --monitor-only
#   ./scripts/docker_memory_test.sh 512m --scrape-only
#
# Requires: docker, .env or .env.local with DATABASE_URL etc.

set -euo pipefail
cd "$(dirname "$0")/.."

# Auto-detect Colima socket if Docker Desktop isn't running
if ! docker info >/dev/null 2>&1; then
  if [ -S "$HOME/.colima/docker.sock" ]; then
    export DOCKER_HOST="unix://$HOME/.colima/docker.sock"
  fi
fi

MEMORY_LIMIT="${1:?Usage: $0 <memory-limit> [--monitor-only|--scrape-only]}"
shift
EXTRA_ARGS="$*"

IMAGE_TAG="crawler-memtest"

echo "==> Building image..."
docker build -t "$IMAGE_TAG" -f Dockerfile .

# Find env file
ENV_FILE=""
for f in .env.local .env; do
  if [ -f "$f" ]; then
    ENV_FILE="$f"
    break
  fi
done

if [ -z "$ENV_FILE" ]; then
  echo "ERROR: No .env or .env.local found. Need DATABASE_URL and Redis credentials."
  exit 1
fi

# Docker --env-file treats quotes as literal chars; strip them for compatibility
CLEAN_ENV_FILE=$(mktemp)
trap "rm -f $CLEAN_ENV_FILE" EXIT
sed 's/^"\(.*\)"$/\1/; s/="\(.*\)"$/=\1/; s/='\''\(.*\)'\''$/=\1/' "$ENV_FILE" > "$CLEAN_ENV_FILE"

echo "==> Running with --memory=$MEMORY_LIMIT (env from $ENV_FILE)"
echo "    Extra args: ${EXTRA_ARGS:-none}"
echo ""

EXIT_CODE=0
docker run \
  --rm \
  --memory="$MEMORY_LIMIT" \
  --memory-swap="$MEMORY_LIMIT" \
  --env-file "$CLEAN_ENV_FILE" \
  "$IMAGE_TAG" \
  uv run python scripts/measure_memory.py $EXTRA_ARGS \
  || EXIT_CODE=$?

echo ""
echo "=========================================="
if [ $EXIT_CODE -eq 137 ]; then
  echo "RESULT: OOM KILLED (exit 137) at ${MEMORY_LIMIT}"
  echo "  -> Need more memory."
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: OK at ${MEMORY_LIMIT}"
  echo "  -> Memory limit is sufficient."
else
  echo "RESULT: FAILED (exit $EXIT_CODE) at ${MEMORY_LIMIT}"
  echo "  -> Check logs above for errors."
fi
echo "=========================================="
exit $EXIT_CODE
