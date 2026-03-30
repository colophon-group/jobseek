#!/usr/bin/env bash
#
# Sweep memory usage across batch sizes.
# Runs scrape-only (the memory-heavy path) at generous memory (2g)
# to measure actual RSS, then reports a table.
#
# Usage:
#   ./scripts/memory_sweep.sh

set -euo pipefail
cd "$(dirname "$0")/.."

# Auto-detect Colima socket if Docker Desktop isn't running
if ! docker info >/dev/null 2>&1; then
  if [ -S "$HOME/.colima/docker.sock" ]; then
    export DOCKER_HOST="unix://$HOME/.colima/docker.sock"
  fi
fi

IMAGE_TAG="crawler-memtest"

echo "==> Building image..."
docker build -q -t "$IMAGE_TAG" -f Dockerfile . >/dev/null 2>&1
echo "    Done."

# Find and clean env file
ENV_FILE=""
for f in .env.local .env; do
  [ -f "$f" ] && ENV_FILE="$f" && break
done
if [ -z "$ENV_FILE" ]; then
  echo "ERROR: No .env or .env.local found."
  exit 1
fi

CLEAN_ENV_FILE=$(mktemp)
trap "rm -f $CLEAN_ENV_FILE" EXIT
sed 's/^"\(.*\)"$/\1/; s/="\(.*\)"$/=\1/; s/='"'"'\(.*\)'"'"'$/=\1/' "$ENV_FILE" > "$CLEAN_ENV_FILE"

# Batch sizes to test
BATCH_SIZES=(10 25 50 100 200)
MEMORY_LIMIT="2g"

echo ""
echo "==> Sweeping batch sizes: ${BATCH_SIZES[*]}"
echo "    Memory limit: $MEMORY_LIMIT (generous, measuring actual RSS)"
echo "    Mode: --scrape-only (memory-heavy path)"
echo ""

# Header
printf "%-12s %-10s %-12s %-12s %-10s %-10s %-8s\n" \
  "BATCH_LIMIT" "SCRAPED" "HEAP_PEAK" "RSS_PEAK" "RSS_DELTA" "DOMAINS" "STATUS"
printf "%-12s %-10s %-12s %-12s %-10s %-10s %-8s\n" \
  "-----------" "-------" "---------" "--------" "---------" "-------" "------"

for BATCH in "${BATCH_SIZES[@]}"; do
  OUTPUT=$(docker run \
    --rm \
    --memory="$MEMORY_LIMIT" \
    --memory-swap="$MEMORY_LIMIT" \
    --env-file "$CLEAN_ENV_FILE" \
    -e "CRAWLER_BATCH_LIMIT=$BATCH" \
    "$IMAGE_TAG" \
    uv run python scripts/measure_memory.py --scrape-only \
    2>&1) || true

  # Parse results
  SCRAPED=$(echo "$OUTPUT" | grep '"event": "memory.scrape_batch"' | sed 's/.*"processed": \([0-9]*\).*/\1/' || echo "?")
  DOMAINS=$(echo "$OUTPUT" | grep '"event": "batch.scrape.start"' | sed 's/.*"domains": \([0-9]*\).*/\1/' || echo "?")
  HEAP_PEAK=$(echo "$OUTPUT" | grep "Peak (total):" | awk '{print $3, $4}' || echo "?")
  RSS_PEAK=$(echo "$OUTPUT" | grep "After (peak):" | awk '{print $3, $4}' || echo "?")
  RSS_DELTA=$(echo "$OUTPUT" | grep "Delta:" | awk '{print $2, $3}' || echo "?")

  if echo "$OUTPUT" | grep -q "OOM\|exit 137\|Killed"; then
    STATUS="OOM"
  elif echo "$OUTPUT" | grep -q "MEMORY REPORT"; then
    STATUS="OK"
  else
    STATUS="ERR"
  fi

  printf "%-12s %-10s %-12s %-12s %-10s %-10s %-8s\n" \
    "$BATCH" "$SCRAPED" "$HEAP_PEAK" "$RSS_PEAK" "$RSS_DELTA" "$DOMAINS" "$STATUS"
done

echo ""
echo "==> Done. Use these RSS numbers to pick your fly.io memory + batch limit."
