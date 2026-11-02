#!/usr/bin/env bash
# Verify the crawler's real private PostgreSQL and Typesense paths without printing endpoints.
set -euo pipefail

POSTGRES_PRIVATE_IP="${JOBSEEK_POSTGRES_PRIVATE_IP:-}"
TYPESENSE_PRIVATE_IP="${JOBSEEK_TYPESENSE_PRIVATE_IP:-}"
for value in "$POSTGRES_PRIVATE_IP" "$TYPESENSE_PRIVATE_IP"; do
  [[ "$value" =~ ^10\.|^192\.168\.|^172\.(1[6-9]|2[0-9]|3[01])\. ]] || {
    echo "A private service IPv4 value is required" >&2
    exit 1
  }
done

if ! exporter_rows="$(
  docker ps \
    --filter label=com.docker.compose.service=exporter \
    --format '{{.ID}}' 2>/dev/null
)"; then
  echo "Cannot select the live exporter container" >&2
  exit 1
fi
exporters=()
if [[ -n "$exporter_rows" ]]; then
  mapfile -t exporters <<<"$exporter_rows"
fi
[[ "${#exporters[@]}" -eq 1 && "${exporters[0]}" =~ ^[0-9a-f]{12,64}$ ]] || {
  echo "Expected exactly one live exporter container" >&2
  exit 1
}

docker exec \
  -e JOBSEEK_EXPECTED_POSTGRES_PRIVATE_IP="$POSTGRES_PRIVATE_IP" \
  -e JOBSEEK_EXPECTED_TYPESENSE_PRIVATE_IP="$TYPESENSE_PRIVATE_IP" \
  "${exporters[0]}" \
  /usr/local/bin/go-typesense-exporter --verify-private-paths
