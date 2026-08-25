#!/usr/bin/env bash
set -euo pipefail

USAGE="crawler-csv-sync-host.sh <40-character revision> <runtime-contract-sha256> [--check-runtime]"
REVISION="${1:?usage: $USAGE}"
RUNTIME_CONTRACT_SHA256="${2:?usage: $USAGE}"
MODE="${3:-}"
[[ "$REVISION" =~ ^[a-f0-9]{40}$ ]] || {
  echo "ERROR: CSV sync revision must be a full lowercase Git commit SHA" >&2
  exit 1
}
[[ "$RUNTIME_CONTRACT_SHA256" =~ ^[a-f0-9]{64}$ ]] || {
  echo "ERROR: CSV sync runtime contract must be a lowercase SHA-256" >&2
  exit 1
}
[[ -z "$MODE" || "$MODE" == --check-runtime ]] || {
  echo "ERROR: usage: $USAGE" >&2
  exit 1
}

DEPLOY_ENV=/home/deploy/.env
ACTIVE_RELEASE_POINTER=/home/deploy/.crawler-active-release
ACTIVE_RELEASE_ROOT=/home/deploy/.crawler-release-generations

verify_runtime_contract() {
  local active_release contract_file
  local mismatch=0
  local -a contract_files=() deployed_contracts=()

  [[ -f "$DEPLOY_ENV" && ! -L "$DEPLOY_ENV" ]] || {
    echo "ERROR: live crawler environment is unavailable or unsafe" >&2
    return 1
  }
  active_release="$(readlink -f "$ACTIVE_RELEASE_POINTER" 2>/dev/null || true)"
  [[ -n "$active_release" && "$active_release" == "$ACTIVE_RELEASE_ROOT/"* && \
    -d "$active_release" && ! -L "$active_release" ]] || {
    echo "ERROR: committed crawler release is unavailable or unsafe" >&2
    return 1
  }

  contract_files=(
    "$DEPLOY_ENV"
    "$active_release/environment.env"
    "$active_release/success.env"
  )
  for contract_file in "${contract_files[@]}"; do
    [[ -f "$contract_file" && ! -L "$contract_file" ]] || {
      echo "ERROR: crawler runtime-contract evidence is unavailable or unsafe" >&2
      return 1
    }
    mapfile -t deployed_contracts < <(
      sed -n 's/^JOBSEEK_RUNTIME_CONTRACT_SHA256=//p' "$contract_file"
    )
    if [[ ${#deployed_contracts[@]} -eq 0 ]]; then
      mismatch=1
      continue
    fi
    if [[ ${#deployed_contracts[@]} -ne 1 || \
      ! "${deployed_contracts[0]}" =~ ^[a-f0-9]{64}$ ]]
    then
      echo "ERROR: committed crawler runtime contract is duplicated or invalid" >&2
      return 1
    fi
    if [[ "${deployed_contracts[0]}" != "$RUNTIME_CONTRACT_SHA256" ]]; then
      mismatch=1
    fi
  done
  if (( mismatch )); then
    echo "WAIT: CSV config requires a crawler runtime that is not committed yet" >&2
    return 75
  fi
}

verify_runtime_contract
if [[ "$MODE" == --check-runtime ]]; then
  exit 0
fi

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
  mapfile -t matches < <(grep -E "^${key}=" "$DEPLOY_ENV" || true)
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
  sed -n 's/^CRAWLER_IMAGE_REF=//p' "$DEPLOY_ENV" 2>/dev/null
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
