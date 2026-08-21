#!/usr/bin/env bash
set -euo pipefail

REVISION="${1:?usage: crawler-csv-sync-host.sh <40-character revision>}"
[[ "$REVISION" =~ ^[a-f0-9]{40}$ ]] || {
  echo "ERROR: CSV sync revision must be a full lowercase Git commit SHA" >&2
  exit 1
}

NAME="crawler-csv-data-sync-${REVISION:0:12}-${BASHPID}"
RUNTIME_ENV="$(mktemp /run/lock/jobseek-csv-sync-env.XXXXXX)"

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -f "$RUNTIME_ENV"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

chmod 0600 "$RUNTIME_ENV"
required_env=(
  LOCAL_DATABASE_URL
  WEB_DATABASE_URL
  TYPESENSE_HOST
  TYPESENSE_PORT
  TYPESENSE_PROTOCOL
  TYPESENSE_OPERATIONS_KEY
)
for key in "${required_env[@]}"; do
  mapfile -t matches < <(grep -E "^${key}=" /home/deploy/.env || true)
  if [[ ${#matches[@]} -ne 1 || -z "${matches[0]#*=}" ]]; then
    echo "ERROR: required CSV sync variable ${key} is missing or duplicated" >&2
    exit 1
  fi
  printf '%s\n' "${matches[0]}" >>"$RUNTIME_ENV"
done
printf '%s\n' \
  'CRAWLER_DB_ROLE=csv-sync' \
  'CRAWLER_DB_POOL_MIN=0' \
  'CRAWLER_DB_POOL_MAX=4' \
  'CRAWLER_DB_POOL_IDLE_SECONDS=60' >>"$RUNTIME_ENV"

mapfile -t image_refs < <(
  sed -n 's/^CRAWLER_IMAGE_REF=//p' /home/deploy/.env 2>/dev/null
)
[[ ${#image_refs[@]} -eq 1 && "${image_refs[0]}" =~ ^ghcr\.io/colophon-group/jobseek-crawler@sha256:[0-9a-f]{64}$ ]] || {
  echo "ERROR: committed crawler image digest is missing, duplicated, or invalid" >&2
  exit 1
}

docker run --rm \
  --name "$NAME" \
  --env-file "$RUNTIME_ENV" \
  --network host \
  -v /home/deploy/csv-overlay:/app/data \
  "${image_refs[0]}" \
  uv run --no-sync crawler sync
