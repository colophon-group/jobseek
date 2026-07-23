#!/usr/bin/env bash
# Run one bounded cross-store reconciliation slice on the crawler host.
set -euo pipefail

ENV_FILE=/home/deploy/.env
LOCK_FILE=/run/lock/jobseek-crawler-mutation.lock
DEPLOYED_SHA_FILE=/var/lib/jobseek-reconciliation/deployed-sha
CONTAINER=jobseek-cross-store-reconciliation
reconciliation_args=(--repair --max-partitions 16)

if (( $# )); then
  if [[ $# -ne 2 || "$1" != "--full-target" ]]; then
    echo "ERROR: usage: $0 [--full-target supabase|typesense]" >&2
    exit 2
  fi
  case "$2" in
    supabase|typesense)
      reconciliation_args=(--repair --full --target "$2")
      ;;
    *)
      echo "ERROR: full target must be supabase or typesense" >&2
      exit 2
      ;;
  esac
fi

[[ -r "$ENV_FILE" ]] || {
  echo "ERROR: crawler deployment environment is unavailable" >&2
  exit 1
}
command -v docker >/dev/null || {
  echo "ERROR: docker is unavailable" >&2
  exit 1
}
command -v flock >/dev/null || {
  echo "ERROR: flock is unavailable" >&2
  exit 1
}
command -v timeout >/dev/null || {
  echo "ERROR: timeout is unavailable" >&2
  exit 1
}
[[ -r "$DEPLOYED_SHA_FILE" ]] || {
  echo "ERROR: reconciliation deployment revision is unavailable" >&2
  exit 1
}
revision="$(<"$DEPLOYED_SHA_FILE")"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || {
  echo "ERROR: reconciliation deployment revision is invalid" >&2
  exit 1
}

exec 9>"$LOCK_FILE"
flock -w 7200 9 || {
  echo "ERROR: timed out waiting for the crawler mutation lock" >&2
  exit 1
}

tag="$(sed -n 's/^CRAWLER_IMAGE_TAG=//p' "$ENV_FILE" | tail -n1)"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "ERROR: deployed crawler image tag is missing or invalid" >&2
  exit 1
}
image="ghcr.io/colophon-group/jobseek-crawler:${tag}"
RUNTIME_ENV=""

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if docker ps -a --format '{{.Names}}' | grep -Fxq "$CONTAINER"; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
  if [[ -n "$RUNTIME_ENV" ]]; then
    rm -f "$RUNTIME_ENV"
  fi
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

RUNTIME_ENV="$(mktemp /run/lock/jobseek-reconciliation-env.XXXXXX)"
chmod 0600 "$RUNTIME_ENV"
required_env=(
  DATABASE_URL
  LOCAL_DATABASE_URL
  TYPESENSE_HOST
  TYPESENSE_PORT
  TYPESENSE_PROTOCOL
  TYPESENSE_OPERATIONS_KEY
)
for key in "${required_env[@]}"; do
  mapfile -t matches < <(grep -E "^${key}=" "$ENV_FILE" || true)
  if [[ ${#matches[@]} -ne 1 || -z "${matches[0]#*=}" ]]; then
    echo "ERROR: required reconciliation variable ${key} is missing or duplicated" >&2
    exit 1
  fi
  printf '%s\n' "${matches[0]}" >>"$RUNTIME_ENV"
done

if docker ps --format '{{.Names}}' | grep -Fxq "$CONTAINER"; then
  echo "ERROR: reconciliation container already exists despite the host lock" >&2
  exit 1
fi
docker rm "$CONTAINER" >/dev/null 2>&1 || true

timeout --foreground --signal=TERM --kill-after=90s 50m docker run --rm \
  --name "$CONTAINER" \
  --init \
  --stop-timeout 60 \
  --env-file "$RUNTIME_ENV" \
  --network host \
  --memory 1g \
  --cpus 1.0 \
  --pids-limit 256 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  -e PYTHONDONTWRITEBYTECODE=1 \
  --label com.docker.compose.project=deploy \
  --label com.docker.compose.service=cross-store-reconciliation \
  --label com.docker.compose.container-number=1 \
  --label com.docker.compose.oneoff=True \
  --label jobseek.maintenance.operation=cross-store-reconciliation \
  --label jobseek.maintenance.issue=5930 \
  --label "jobseek.maintenance.revision=${revision}" \
  --label jobseek.maintenance.budget-seconds=3000 \
  "$image" \
  /app/.venv/bin/crawler reconcile "${reconciliation_args[@]}"
